#!/usr/bin/python
import logging
import pika
import json
import config
import netifaces, netaddr
import sys, time

LOG_FORMAT = ('%(levelname) -10s %(asctime)s %(name) -30s %(funcName) '
              '-35s %(lineno) -5d: %(message)s')
LOGGER = logging.getLogger(__name__)

logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)

try:
     import Adafruit_DHT
except ImportError:
     LOGGER.warning("Using Fake sensor")
     class Adafruit_DHT(object):
          @staticmethod
          def read(sensor, pin):
               LOGGER.info("reading from fake sensor and pin %d" % pin)
               return 20, 60

          AM2302 = 1

class NodePublisher(object):
    PUBLISH_INTERVAL = 5
    QUEUE = 'temp'
    ROUTING_KEY = QUEUE
    URLS = [config.URL]

    def __init__(self):
        """Setup the example publisher object, passing in the URL we will use
        to connect to RabbitMQ.

        """
        self._connection = None
        self._channel = None
        self._deliveries = []
        self._acked = 0
        self._nacked = 0
        self._message_number = 0
        self._stopping = False
        self._closing = False
        self._url_offset = 0

    def connect(self):
        """This method connects to RabbitMQ, returning the connection handle.
        When the connection is established, the on_connection_open method
        will be invoked by pika.

        :rtype: pika.SelectConnection

        """
        url = self.URLS[self._url_offset]
        self._url_offset += 1
        if self._url_offset == len(self.URLS):
            self._url_offset = 0
        LOGGER.info('Connecting to %s', url)
        return pika.SelectConnection(pika.URLParameters(url),
                                     self.on_connection_open,
                                     False)

    def close_connection(self):
        """This method closes the connection to RabbitMQ."""
        LOGGER.info('Closing connection')
        self._closing = True
        self._connection.close()

    def add_on_connection_close_callback(self):
        """This method adds an on close callback that will be invoked by pika
        when RabbitMQ closes the connection to the publisher unexpectedly.

        """
        LOGGER.info('Adding connection close callback')
        self._connection.add_on_close_callback(self.on_connection_closed)

    def on_connection_closed(self, connection, reply_code, reply_text):
        """This method is invoked by pika when the connection to RabbitMQ is
        closed unexpectedly. Since it is unexpected, we will reconnect to
        RabbitMQ if it disconnects.

        :param pika.connection.Connection connection: The closed connection obj
        :param int reply_code: The server provided reply_code if given
        :param str reply_text: The server provided reply_text if given

        """
        self._channel = None
        if self._closing:
            self._connection.ioloop.stop()
        else:
            LOGGER.warning('Connection closed, reopening in 5 seconds: (%s) %s',
                           reply_code, reply_text)
            self._connection.add_timeout(5, self.reconnect)

    def on_connection_open(self, unused_connection):
        """This method is called by pika once the connection to RabbitMQ has
        been established. It passes the handle to the connection object in
        case we need it, but in this case, we'll just mark it unused.

        :type unused_connection: pika.SelectConnection

        """
        LOGGER.info('Connection opened')
        self.add_on_connection_close_callback()
        self.open_channel()

    def reconnect(self):
        """Will be invoked by the IOLoop timer if the connection is
        closed. See the on_connection_closed method.

        """
        # This is the old connection IOLoop instance, stop its ioloop
        self._connection.ioloop.stop()

        # Create a new connection
        self._connection = self.connect()

        # There is now a new connection, needs a new ioloop to run
        self._connection.ioloop.start()

    def add_on_channel_close_callback(self):
        """This method tells pika to call the on_channel_closed method if
        RabbitMQ unexpectedly closes the channel.

        """
        LOGGER.info('Adding channel close callback')
        self._channel.add_on_close_callback(self.on_channel_closed)

    def on_channel_closed(self, channel, reply_code, reply_text):
        """Invoked by pika when RabbitMQ unexpectedly closes the channel.
        Channels are usually closed if you attempt to do something that
        violates the protocol, such as re-declare an exchange or queue with
        different parameters. In this case, we'll close the connection
        to shutdown the object.

        :param pika.channel.Channel: The closed channel
        :param int reply_code: The numeric reason the channel was closed
        :param str reply_text: The text reason the channel was closed

        """
        LOGGER.warning('Channel was closed: (%s) %s', reply_code, reply_text)
        self._deliveries = []
        self._message_number = 0
        if not self._closing:
            self._connection.close()

    def on_channel_open(self, channel):
        """This method is invoked by pika when the channel has been opened.
        The channel object is passed in so we can make use of it.

        Since the channel is now open, we'll declare the exchange to use.

        :param pika.channel.Channel channel: The channel object

        """
        LOGGER.info('Channel opened')
        self._channel = channel
        self.add_on_channel_close_callback()
        self.setup_queue(self.QUEUE)

    def setup_queue(self, queue_name):
        """Setup the queue on RabbitMQ by invoking the Queue.Declare RPC
        command. When it is complete, the on_queue_declareok method will
        be invoked by pika.

        :param str|unicode queue_name: The name of the queue to declare.

        """
        LOGGER.info('Declaring queue %s', queue_name)
        self._channel.queue_declare(self.on_queue_declareok, queue_name, durable = True)

    def on_queue_declareok(self, method_frame):
        """Method invoked by pika when the Queue.Declare RPC call made in
        setup_queue has completed.

        :param pika.frame.Method method_frame: The Queue.DeclareOk frame

        """
        self.start_publishing()

    def on_delivery_confirmation(self, method_frame):
        """Invoked by pika when RabbitMQ responds to a Basic.Publish RPC
        command, passing in either a Basic.Ack or Basic.Nack frame with
        the delivery tag of the message that was published. The delivery tag
        is an integer counter indicating the message number that was sent
        on the channel via Basic.Publish. Here we're just doing house keeping
        to keep track of stats and remove message numbers that we expect
        a delivery confirmation of from the list used to keep track of messages
        that are pending confirmation.

        :param pika.frame.Method method_frame: Basic.Ack or Basic.Nack frame

        """
        confirmation_type = method_frame.method.NAME.split('.')[1].lower()
        LOGGER.info('Received %s for delivery tag: %i',
                    confirmation_type,
                    method_frame.method.delivery_tag)
        if confirmation_type == 'ack':
            self._acked += 1
        elif confirmation_type == 'nack':
            self._nacked += 1
        self._deliveries.remove(method_frame.method.delivery_tag)
        LOGGER.info('Published %i messages, %i have yet to be confirmed, '
                    '%i were acked and %i were nacked',
                    self._message_number, len(self._deliveries),
                    self._acked, self._nacked)

    def enable_delivery_confirmations(self):
        """Send the Confirm.Select RPC method to RabbitMQ to enable delivery
        confirmations on the channel. The only way to turn this off is to close
        the channel and create a new one.

        When the message is confirmed from RabbitMQ, the
        on_delivery_confirmation method will be invoked passing in a Basic.Ack
        or Basic.Nack method from RabbitMQ that will indicate which messages it
        is confirming or rejecting.

        """
        LOGGER.info('Issuing Confirm.Select RPC command')
        self._channel.confirm_delivery(self.on_delivery_confirmation)

    def publish_message(self):
        """If the class is not stopping, publish a message to RabbitMQ,
        appending a list of deliveries with the message number that was sent.
        This list will be used to check for delivery confirmations in the
        on_delivery_confirmations method.

        Once the message has been sent, schedule another message to be sent.
        The main reason I put scheduling in was just so you can get a good idea
        of how the process is flowing by slowing down and speeding up the
        delivery intervals by changing the PUBLISH_INTERVAL constant in the
        class.

        """
        if self._stopping:
            return

        humidity, temperature = Adafruit_DHT.read(Adafruit_DHT.AM2302, config.PIN)
        if temperature == None:
            LOGGER.warn("Can't get temperature properly currently")

        def data_format(value):
            if value == None:
                return None
            return "%.1f" % value

        ifaces = [netifaces.ifaddresses(intf) for intf in netifaces.interfaces()]
        addresses = []
        for iface in ifaces:
            if netifaces.AF_INET not in iface:
                continue
            addresses.extend([addr['addr'] for addr in iface[netifaces.AF_INET]])
        addresses = [str(ip) for ip in [netaddr.IPAddress(x) for x in addresses] if ip.is_unicast() and not ip.is_reserved()]

        message = {
            "temp" : data_format(temperature),
            "humid" : data_format(humidity),
            "id": config.NODE_ID,
            "addresses": addresses
        }
        properties = pika.BasicProperties(app_id=config.NODE_ID,
                                          content_type='text/plain')
        self._channel.basic_publish('', self.ROUTING_KEY,
                                    json.dumps(message, ensure_ascii=False),
                                    properties)
        self._message_number += 1
        self._deliveries.append(self._message_number)
        LOGGER.info('Published message # %i', self._message_number)
        self.schedule_next_message()

    def schedule_next_message(self):
        """If we are not closing our connection to RabbitMQ, schedule another
        message to be delivered in PUBLISH_INTERVAL seconds.

        """
        if self._stopping:
            return
        LOGGER.info('Scheduling next message for %0.1f seconds',
                    self.PUBLISH_INTERVAL)
        self._connection.add_timeout(self.PUBLISH_INTERVAL,
                                     self.publish_message)

    def start_publishing(self):
        """This method will enable delivery confirmations and schedule the
        first message to be sent to RabbitMQ

        """
        LOGGER.info('Issuing consumer related RPC commands')
        self.enable_delivery_confirmations()
        self.schedule_next_message()

    def close_channel(self):
        """Invoke this command to close the channel with RabbitMQ by sending
        the Channel.Close RPC command.

        """
        LOGGER.info('Closing the channel')
        if self._channel:
            self._channel.close()

    def open_channel(self):
        """This method will open a new channel with RabbitMQ by issuing the
        Channel.Open RPC command. When RabbitMQ confirms the channel is open
        by sending the Channel.OpenOK RPC reply, the on_channel_open method
        will be invoked.

        """
        LOGGER.info('Creating a new channel')
        self._connection.channel(on_open_callback=self.on_channel_open)

    def run(self):
        """Run the example code by connecting and then starting the IOLoop.

        """
        self._connection = self.connect()
        self._connection.ioloop.start()

    def stop(self):
        """Stop the example by closing the channel and connection. We
        set a flag here so that we stop scheduling new messages to be
        published. The IOLoop is started because this method is
        invoked by the Try/Catch below when KeyboardInterrupt is caught.
        Starting the IOLoop again will allow the publisher to cleanly
        disconnect from RabbitMQ.

        """
        LOGGER.info('Stopping')
        self._stopping = True
        self.close_channel()
        self.close_connection()
        self._connection.ioloop.start()
        LOGGER.info('Stopped')

def main():
    LOGGER.info("This is %s" % config.NODE_ID)
    while True:
        example = NodePublisher()
        try:
            example.run()
        except KeyboardInterrupt:
            example.stop()
            sys.exit(-1)
        except Exception,e:
            LOGGER.error("Error in sensor node: %s" % e)
            LOGGER.error("Restarting in 5 seconds...")
            time.sleep(5)

if __name__ == '__main__':
    main()
