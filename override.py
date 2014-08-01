#!/usr/bin/evn python

# This script allows an RPI with a button to act as an override for the whereabouts light control
# Must be run as root!

import sys
import time

import RPi.GPIO as GPIO
from uuid import getnode as get_mac

import urllib2
import json
import httplib

USAGE ="""
Listens for button presses on a Raspberry Pi and POSTs events to GATD

To perform continuous monitoring, please specify the location being monitored.
Locations should be specified in the format:
    University|Building|Room

The following locations have been seen historically:"""
LOCATION = ""

BUTTON_PROFILE_ID = '9YWtcF3MFW'
BUTTON_GET_ADDR = 'http://inductor.eecs.umich.edu:8085/explore/profile/' + BUTTON_PROFILE_ID
BUTTON_POST_ADDR = 'http://inductor.eecs.umich.edu:8081/' + BUTTON_PROFILE_ID

LIGHT_PROFILE_ID = 'UbkhN72jvp'

BTN_PIN = 25
LIGHT_PIN = 24
# gets the mac address of the device, in hex, and cuts out the prepended 0x
#   and appended L
DEV_MAC_ADDR = hex(get_mac())[2:-1]

def main():
    global BTN_PIN, LIGHT_PIN, DEV_MAC_ADDR, LOCATION, LIGHT_PROFILE_ID

    # get location from the user
    LOCATION = get_location()
    print("Running button override at " + LOCATION)

    # setup GPIO pin
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(BTN_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(LIGHT_PIN, GPIO.OUT)

    # start thread to receive data from GATD
    query = {'location_str': LOCATION}
    message_queue = Queue.Queue()
    ReceiverThread(LIGHT_PROFILE_ID, query, 'presence', message_queue)

    # data to be used is constant. Note that a timestamp is automatically
    #   appended
    data = {
            'location_str': LOCATION,
            'device_id': DEV_MAC_ADDR,
            'button_id': BTN_PIN
            }

    while True:
        # wait for a button press
        GPIO.wait_for_edge(BTN_PIN, GPIO.FALLING)

        # check that the button is truly low. I've been getting a lot of false
        #   positives for whatever reason
        time.sleep(0.1)
        if GPIO.input(BTN_PIN) != 0:
            continue

        # transmit message to GATD
        print("Button Pressed!")
        post_to_gatd(data)

        # don't send another message for 1 second to ensure there is no
        #   bouncing and that the button has been released (multiple messages
        #   is acceptable but undesirable)
        time.sleep(1)

def post_to_gatd(data):
    global BUTTON_POST_ADDR

    try:
        req = urllib2.Request(BUTTON_POST_ADDR)
        req.add_header('Content-Type', 'application/json')
        response = urllib2.urlopen(req, json.dumps(data))
    except (httplib.BadStatusLine, urllib2.URLError), e:
        # ignore error and carry on
        print("Failure to POST to GATD: " + str(e))

def get_location():
    global USAGE

    # get location selection from user
    if len(sys.argv) != 2 or sys.argv[1] == '':
        print(USAGE)

        # get a list of previously monitored locations
        locations = query_gatd_explorer('location_str')

        index = 0
        for location in locations:
            print("\t[" + str(index) + "]: " + location)
            index += 1
        print("")

        user_input = raw_input("Select a location or enter a new one: ")
        if user_input == '':
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

def query_gatd_explorer(key):
    global BUTTON_GET_ADDR

    # query GATD explorer to find scan locations
    try:
        req = urllib2.Request(BUTTON_GET_ADDR)
        response = urllib2.urlopen(req)
        json_data = json.loads(response.read())
    except (httplib.BadStatusLine, urllib2.URLError), e:
        print("Connection to GATD failed: " + str(e))
        return ['None']

    if key in json_data:
        return json_data['location_str'].keys()
    else:
        return ['None']


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
        #self.message_queue.put([self.data_type, args[0]])

        # Since I only have one thread, I'm just going to do special functions here
        global LIGHT_PIN
        data = args[0]
        if 'action' in data:
            if data['action'] == 'off':
                GPIO.output(LIGHT_PIN, GPIO.HIGH)
            else:
                GPIO.output(LIGHT_PIN, GPIO.LOW)


if __name__ == "__main__":
    main()

