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
LIGHT_COMMAND_PROFILE_ID = 'MUs0XwOiyp'
LIGHT_PROFILE_ID = 'UbkhN72jvp'
LIGHT_POST_ADDR = 'http://inductor.eecs.umich.edu:8081/' + LIGHT_PROFILE_ID

ACMEpp_IPV6 = '2607:f018:800:10f:c298:e541:4310:1'
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
    ReceiverThread(LIGHT_COMMAND_PROFILE_ID, query, 'command', message_queue)

    # Create ACME++ object
    acmepp = ACMEpp(ACMEpp_IPV6, ACMEpp_PORT)

    # process packets
    absence_start = 0
    auto_light_state = 'On'

    temp_override_start = 0
    prev_temp_override_end = 0
    temp_override_duration = 30
    manual_override  = False
    manual_light_state = 'On'

    state_change = True
    while True:

        # process light states
        # Note: these command the lights at a minimum of every 10 seconds
        #   (timeout period) and a maximum of however fast packets arrive.
        #   This can get kind of fast with macScanner, so it is rate limited
        #   in the acmepp class to one real transmission per 10 seconds. This
        #   is okay because we will send another packet within a maximum of 10
        #   seconds, and the lights don't change that quickly
        if manual_override == True or temp_override_start != 0:
            # manual control of lights
            if manual_light_state == 'On':
                acmepp.setOn()
                if state_change == True:
                    print(cur_datetime() + ": Manual lights on")
            else:
                acmepp.setOff()
                if state_change == True:
                    print(cur_datetime() + ": Manual lights off")
        else:
            # automatic control of lights
            if auto_light_state == 'On':
                acmepp.setOn()
                if state_change == True:
                    print(cur_datetime() + ": Automatic lights on")
            else:
                acmepp.setOff()
                if state_change == True:
                    print(cur_datetime() + ": Automatic lights off")

        state_change = False
        pkt = None
        try:
            # Pull data from message queue
            [data_type, pkt] = message_queue.get(timeout=10)
        except Queue.Empty:
            # No data has been seen, handle timeouts
            pass

        current_time = int(round(time.time()))

        # turn off override mode if it's been a full duration
        if temp_override_start != 0 and (current_time - temp_override_start) > temp_override_duration*60:
            temp_override_start = 0
            prev_temp_override_end = current_time
            state_change = True
            print(cur_datetime() + ": Override timed out")

        # turn off lights if it's been ten minutes with no people
        if absence_start != 0 and (current_time - absence_start) > 10*60:
            absence_start = 0
            auto_light_state = 'Off'
            state_change = True
            print(cur_datetime() + ": No one seen for ten minutes")

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
                    # this is the right button, do action based on state
                    if manual_override == False:
                        # determine how long the duration should be
                        if (prev_temp_override_end != 0 and
                                (current_time - prev_temp_override_end) < 10*60):
                            # if the button gets pressed again within 10 minutes of the timeout,
                            #   double the duration of the temporary override
                            temp_override_duration *= 2
                        else:
                            # otherwise return to a normal duration
                            temp_override_duration = 30

                        # enable lights for some time
                        temp_override_start = current_time
                        prev_temp_override_end = 0
                        manual_light_state = 'On'
                        state_change = True
                        print(cur_datetime() + ": Button Override! Lights on for 30 minutes")
                    else:
                        # resume automatic control
                        temp_override_start = 0
                        manual_override = False
                        manual_light_state = 'On'
                        state_change = True
                        print(cur_datetime() + ": Button Override! Control resumed")

        # Presence data
        # This data comes from Whereabouts in single packets containing a list
        #   of the people current present
        if data_type == 'presence' and 'person_list' in pkt:
            if len(pkt['person_list']) == 0:
                # no one is here! start a count and wait for 10 minutes
                #   before actually turning off the lights
                if absence_start == 0 and auto_light_state == 'On':
                    absence_start = current_time
            else:
                # someone is here! make sure the lights are on and stop
                #   any running counter
                absence_start = 0
                if auto_light_state == 'Off':
                    auto_light_state = 'On'
                    state_change = True
                    print(cur_datetime() + ": Someone is seen!")

        # Command data
        # This data comes from commands sent by the 4908 script. Commands
        #   include 'stay_on', 'stay_off', 'resume', 'on', and 'off'
        if data_type == 'command' and 'light_command' in pkt:
            if pkt['light_command'] == 'on':
                # temporary override of lights
                temp_override_start = current_time
                manual_override = False
                manual_light_state = 'On'
                state_change = True
                print(cur_datetime() + ": Command override! Temporary on")
            if pkt['light_command'] == 'off':
                # temporary override of lights
                temp_override_start = current_time
                manual_override = False
                manual_light_state = 'Off'
                state_change = True
                print(cur_datetime() + ": Command override! Temporary off")
            if pkt['light_command'] == 'resume':
                # turn off manual_control
                temp_override_start = 0
                manual_override = False
                state_change = True
                print(cur_datetime() + ": Command override! Resume control")
            if pkt['light_command'] == 'stay_on':
                # permanent manual control
                temp_override_start = 0
                manual_override = True
                manual_light_state = 'On'
                state_change = True
                print(cur_datetime() + ": Command override! Stay on")
            if pkt['light_command'] == 'stay_off':
                # permanent manual control
                temp_override_start = 0
                manual_override = True
                manual_light_state = 'Off'
                state_change = True
                print(cur_datetime() + ": Command override! Stay off")


def cur_datetime():
    return time.strftime("%m/%d/%Y %H:%M")

def post_to_gatd(data):
    global LIGHT_POST_ADDR

    try:
        req = urllib2.Request(LIGHT_POST_ADDR)
        req.add_header('Content-Type', 'application/json')
        response = urllib2.urlopen(req, json.dumps(data))
    except (httplib.BadStatusLine, urllib2.URLError), e:
        # ignore error and carry on
        print("Failure to POST to GATD: " + str(e))

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
        self.last_post_time = 0

        # actually, it's unknown, but good enough
        self.on = False

    def setOn (self):
        # only actually send an on packet if rate-limiting say its okay
        if (self._should_transmit()):
            self._post_action('on')
            self.s.sendto('\x01'.encode(), (self.addr, self.port))
            self.on = True

    def setOff (self):
        # only actually send an on packet if rate-limiting say its okay
        if (self._should_transmit()):
            self._post_action('off')
            self.s.sendto('\x02'.encode(), (self.addr, self.port))
            self.on = False

    def _should_transmit (self):
        # rate-limiting packet transmissions to one per 10 seconds
        if (time.time() - self.last_post_time) > 10:
            self.last_post_time = time.time()
            return true
        return false

    def _post_action (self, action):
        data = {
                'action': action,
                'acmepp_addr': self.addr,
                'acmepp_port': self.port
                }
        post_to_gatd(data)


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

