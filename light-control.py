#!/usr/bin/env python

import sys
import time
import Queue
from threading import Thread

import json
import urllib2
import httplib
import socket

try:
    import socketIO_client as sioc
except ImportError:
    print('Could not import the socket.io client library.')
    print('sudo pip install socketIO-client')
    sys.exit(1)

USAGE ="""
Controls lights using an ACME++ based on occupancy data from GATD

To perform continuous monitoring, please specify the location being monitored.
Locations should be specified in the format:
    University|Building|Room

The following locations are monitored for occupancy:"""
LOCATION = ""

PRESENCE_PROFILE_ID = 'hsYQx8blbd'
BUTTON_PROFILE_ID = '9YWtcF3MFW'

ACMEpp_IPV6 = '2001:470:1f11:131a:c298:e541:4310:1'
ACMEpp_PORT = 47652

def main():
    global LOCATION, USAGE, BUTTON_PROFILE_ID, PRESENCE_PROFILE_ID

    # get location from the user
    LOCATION = get_location(USAGE, BUTTON_PROFILE_ID)
    print("Running light control at " + LOCATION)

    # start threads to receive data from GATD
    query = {'location_str': LOCATION}
    message_queue = Queue.Queue()
    ReceiverThread(PRESENCE_PROFILE_ID, query, 'presence', message_queue)
    ReceiverThread(BUTTON_PROFILE_ID, query, 'button', message_queue)

    # Create ACME++ object
    acmepp = ACMEpp(ACMEpp_IPV6, ACMEpp_PORT)
    acmepp.setOff()

    # process packets
    override_time = 0
    no_people_time = 0
    while True:
        timeout = False
        pkt = None
        try:
            # Pull data from message queue
            [data_type, pkt] = message_queue.get(timeout=10)
        except Queue.Empty:
            # No data has been seen, handle timeouts
            timeout = True

        current_time = int(round(time.time()))

        # turn off override mode if it's been a half an hour
        if override_time != 0 and (current_time - override_time) > 30*60:
            override_time = 0
            print(cur_datetime() + ": Override timed out")

        # turn off lights if it's been ten minutes with no people and the
        #   override is not enabled
        if (no_people_time != 0 and override_time == 0 and
                (current_time - no_people_time) > 10*60):
            no_people_time = 0
            acmepp.setOff()
            print(cur_datetime() + ": No occupancy for 10 minutes")

        # skip packet if it doesn't contain enough data to use
        if (pkt == None or 'location_str' not in pkt or 'time' not in pkt):
            continue

        # skip packet if not for this location
        if pkt['location_str'] != LOCATION:
            continue

        # Button data
        # This data comes in single packets idntifying that a button press has
        #   occurred. On the appropriate button press, light control will be
        #   overriden for a half-hour and the lights will be turned on
        if data_type == 'button':
            if 'device_id' in pkt and pkt['device_id'] == 'b827eb0a2b8f':
                if 'button_id' in pkt and pkt['button_id'] == 25:
                    override_time = current_time
                    no_people_time = 0
                    acmepp.setOn()
                    print(cur_datetime() + ": Button Override!")

        # Presence data
        # This data comes from Whereabouts in single packets containing a list
        #   of the people current present
        if data_type == 'presence':
            if 'person_list' in pkt:
                if len(pkt['person_list']) == 0:
                    # no one is here! start a count and wait for 10 minutes
                    #   before actually turning off the lights
                    if no_people_time == 0:
                        no_people_time = current_time
                        print(cur_datetime() + ": No one here")
                    else:
                        print(cur_datetime() + ": Still no one here")

                else:
                    # someone is here! make sure the lights are on and stop
                    #   any running counter
                    no_people_time = 0
                    acmepp.setOn()
                    print(cur_datetime() + ": Occupany detected")

def cur_datetime():
    return time.strftime("%m/%d/%Y %H:%M")

def get_location(usage, profile_id):

    # get location selection from user
    if len(sys.argv) != 2 or sys.argv[1] == '':
        print(usage)

        # get a list of previously monitored locations
        locations = query_gatd_explorer(profile_id, 'location_str')

        index = 0
        for location in locations:
            print("\t[" + str(index) + "]: " + location)
            index += 1
        print("")

        user_input = raw_input("Select a location or enter a new one: ")
        if user_input == '' or user_input == 'None':
            print("Invalid selection")
            sys.exit(1)
        if user_input.isdigit():
            user_input = int(user_input)
            if 0 <= user_input < index:
                return locations[user_input]
            else:
                print("Invalid selection")
                sys.exit(1)
        else:
            return user_input
    else:
        return sys.argv[1]

def query_gatd_explorer(profile_id, key):
    explorer_addr = 'http://inductor.eecs.umich.edu:8085/explore/profile/' + profile_id

    # query GATD explorer to find scan locations
    try:
        req = urllib2.Request(explorer_addr)
        response = urllib2.urlopen(req)
    except (httplib.BadStatusLine, urllib2.URLError), e:
        print("Connection to GATD failed: " + str(e))
        return ['None']

    json_data = json.loads(response.read())
    if key in json_data:
        return json_data[key].keys()
    else:
        return ['None']


class ACMEpp ():

    def __init__ (self, ipv6_addr, port):
        self.s = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM)
        self.addr = ipv6_addr
        self.port = port

    def setOn (self):
        self.s.sendto('\x01'.encode(), (self.addr, self.port))

    def setOff (self):
        self.s.sendto('\x02'.encode(), (self.addr, self.port))


class ReceiverThread (Thread):
    SOCKETIO_HOST = 'inductor.eecs.umich.edu'
    SOCKETIO_PORT = 8082
    SOCKETIO_NAMESPACE = 'stream'


    def __init__(self, profile_id, query, data_type, message_queue):
        super(ReceiverThread, self).__init__()
        self.daemon = True

        # init data
        self.profile_id = profile_id
        self.data_type = data_type
        self.message_queue = message_queue
        self.stream_namespace = None

        # make query. Note that this overrides the profile id with the user's
        #   choice if specified in query
        profile_query = {'profile_id': profile_id}
        self.query = dict(list(profile_query.items()) + list(query.items()))

        # start thread
        self.start()

    def run(self):
        while True:
            try:
                socketIO = sioc.SocketIO(self.SOCKETIO_HOST, self.SOCKETIO_PORT)
                self.stream_namespace = socketIO.define(StreamReceiver,
                        '/{}'.format(self.SOCKETIO_NAMESPACE))
                self.stream_namespace.set_data(self.query, self.data_type, self.message_queue,
                        self.stream_namespace)
                socketIO.wait()
            except sioc.exceptions.ConnectionError:
                # ignore error and continue
                socketIO.disconnect()


class StreamReceiver (sioc.BaseNamespace):

    def set_data (self, query, data_type, message_queue, stream_namespace):
        self.query = query
        self.data_type = data_type
        self.message_queue = message_queue
        self.stream_namespace = stream_namespace

    def on_reconnect (self):
        if 'time' in query:
            del query['time']
        self.stream_namespace.emit('query', self.query)

    def on_connect (self):
        self.stream_namespace.emit('query', self.query)

    def on_data (self, *args):
        # data received from gatd. Push to msg_q
        self.message_queue.put([self.data_type, args[0]])


if __name__ == "__main__":
    main()

