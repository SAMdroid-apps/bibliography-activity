# Copyright (c) 2015 Walter Bender
# Copyright (C) 2015 Sam Parkinson
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# You should have received a copy of the GNU General Public License
# along with this library; if not, write to the Free Software
# Foundation, 51 Franklin Street, Suite 500 Boston, MA 02110-1335 USA

import json
from base64 import b64encode, b64decode
from gi.repository import GObject

from telepathy.interfaces import CHANNEL_INTERFACE
from telepathy.interfaces import CHANNEL_INTERFACE_GROUP
from telepathy.interfaces import CHANNEL_TYPE_TEXT
from telepathy.interfaces import CONN_INTERFACE_ALIASING
from telepathy.constants import CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES
from telepathy.constants import CHANNEL_TEXT_MESSAGE_TYPE_NORMAL
from telepathy.client import Connection

from sugar3.presence import presenceservice
from sugar3.activity.activity import SCOPE_PRIVATE
from sugar3.graphics.alert import NotifyAlert, Alert

from gettext import gettext as _

import logging
_logger = logging.getLogger('text-channel-wrapper')

'''
Usage:

(1) Create a Text Channel by passing in the
self.shared_activity.telepathy_text_chan and
self.shared_activity.telepathy_conn.
(2) Set a callback for when messages are receieved.
(3) Send messages with the post method.
'''

# TODO note 'action' can't start with !!
ACTION_INIT_REQUEST = '!!ACTION_INIT_REQUEST'
ACTION_INIT_RESPONSE = '!!ACTION_INIT_RESPONSE'


class CollabWrapper(GObject.GObject):

    message = GObject.Signal('message', arg_types=([object, object]))
    joined = GObject.Signal('joined')
    buddy_joined = GObject.Signal('buddy_joined')
    buddy_left = GObject.Signal('buddy_left')  # FIXME

    def __init__(self, activity):
        GObject.GObject.__init__(self)
        self.activity = activity
        self.shared_activity = activity.shared_activity
        self.leader = False
        self.init_waiting = False
        self._text_channel = None

        # self.owner = presenceservice.get_instance().get_owner()

        # Some glue to know if we are launching, joining, or resuming
        # a shared activity.
        if self.shared_activity:
            # We're joining the activity.
            self.activity.connect("joined", self.__joined_cb)

            if self.activity.get_shared():
                _logger.debug('calling _joined_cb')
                self.__joined_cb(self)
            else:
                _logger.debug('Please wait')
                self._alert(_('Please wait'), _('Starting connection...'))
        else:
            if not self.activity.metadata or self.activity.metadata.get(
                    'share-scope', SCOPE_PRIVATE) == \
                    SCOPE_PRIVATE:
                # We are creating a new activity instance.
                _logger.debug('Off-line')
            else:
                # We are sharing an old activity instance.
                _logger.debug('On-line')
                self._alert(_('Resuming shared activity...'),
                            _('Please wait for the connection...'))
            self.activity.connect('shared', self.__shared_cb)

    def _alert(self, title, text=None):
        a = NotifyAlert(timeout=5)
        a.props.title = title
        # FIXME a.props.text = text
        self.activity.add_alert(a)
        a.connect('response', lambda a, r: self.activity.remove_alert(a))
        a.show()

    def __shared_cb(self, sender):
        ''' Callback for when activity is shared. '''
        self.shared_activity = self.activity.shared_activity
        self._setup_text_channel()

        self.leader = True
        _logger.debug('I am sharing...')
        self._alert(_('shared cb'), ('I am sharing'));
        
    def __joined_cb(self, sender):
        '''Callback for when an activity is joined.'''
        self.shared_activity = self.activity.shared_activity
        if not self.shared_activity:
            return
        _logger.debug('Joined a shared chat')
        self._alert(_('joined cb'), ('I am joining'));

        # WTF does this do?
        # for buddy in self.shared_activity.get_joined_buddies():
        #    self._buddy_already_exists(buddy)
        self._setup_text_channel()
        self.init_waiting = True
        self.post({'action': ACTION_INIT_REQUEST})

        _logger.debug('I joined a shared activity.')
        self.joined.emit()

    def _setup_text_channel(self):
        ''' Set up a text channel to use for collaboration. '''
        self._text_channel = TextChannelWrapper(
            self.shared_activity.telepathy_text_chan,
            self.shared_activity.telepathy_conn)

        # Tell the text channel what callback to use for incoming
        # text messages.
        self._text_channel.set_received_callback(self.__received_cb)

        # Tell the text channel what callbacks to use when buddies
        # come and go.
        self.shared_activity.connect('buddy-joined', self.__buddy_joined_cb)
        self.shared_activity.connect('buddy-left', self.__buddy_left_cb)

    def __received_cb(self, buddy, msg):
        '''Process a message when it is received.'''
        action = msg.get('action')
        if action == ACTION_INIT_REQUEST and self.leader:
            data = self.activity.get_data()
            self.post({'action': ACTION_INIT_RESPONSE, 'data': data})
            return
        elif action == ACTION_INIT_RESPONSE and self.init_waiting:
            data = msg['data']
            self.activity.read_data(data)
            self.init_waiting = False
            return

        if buddy:
            if type(buddy) is dict:
                nick = buddy['nick']
            else:
                nick = buddy.props.nick
        else:
            nick = '???'
        _logger.error('Received message from %s: %r' % (nick, msg))
        self.message.emit(buddy, msg)

    def post(self, msg):
        _logger.error('Posting msg %r', msg)
        if self._text_channel is not None:
            _logger.error('\tLegit post')
            self._text_channel.post(msg)

    def __buddy_joined_cb(self, sender, buddy):
        '''A buddy joined.'''
        return
        if buddy == self.owner:
            return

    def __buddy_left_cb(self, sender, buddy):
        '''A buddy left.'''
        return
        if buddy == self.owner:
            return


class TextChannelWrapper(object):
    '''Wrapper for a telepathy Text Channel'''

    def __init__(self, text_chan, conn):
        '''Connect to the text channel'''
        self._activity_cb = None
        self._activity_close_cb = None
        self._text_chan = text_chan
        self._conn = conn
        self._signal_matches = []
        m = self._text_chan[CHANNEL_INTERFACE].connect_to_signal(
            'Closed', self._closed_cb)
        self._signal_matches.append(m)

    def post(self, msg):
        if msg is not None:
            _logger.debug('post')
            self._send(json.dumps(msg))

    def _send(self, text):
        '''Send text over the Telepathy text channel.'''
        _logger.debug('sending %s' % text)

        text = b64encode(text)

        if self._text_chan is not None:
            self._text_chan[CHANNEL_TYPE_TEXT].Send(
                CHANNEL_TEXT_MESSAGE_TYPE_NORMAL, text)

    def close(self):
        '''Close the text channel.'''
        _logger.debug('Closing text channel')
        try:
            self._text_chan[CHANNEL_INTERFACE].Close()
        except Exception:
            _logger.debug('Channel disappeared!')
            self._closed_cb()

    def _closed_cb(self):
        '''Clean up text channel.'''
        for match in self._signal_matches:
            match.remove()
        self._signal_matches = []
        self._text_chan = None
        if self._activity_close_cb is not None:
            self._activity_close_cb()

    def set_received_callback(self, callback):
        '''Connect the function callback to the signal.

        callback -- callback function taking buddy and text args
        '''
        if self._text_chan is None:
            return
        self._activity_cb = callback
        m = self._text_chan[CHANNEL_TYPE_TEXT].connect_to_signal(
            'Received', self._received_cb)
        self._signal_matches.append(m)

    def handle_pending_messages(self):
        '''Get pending messages and show them as received.'''
        for identity, timestamp, sender, type_, flags, text in \
            self._text_chan[
                CHANNEL_TYPE_TEXT].ListPendingMessages(False):
            self._received_cb(identity, timestamp, sender, type_, flags, text)

    def _received_cb(self, identity, timestamp, sender, type_, flags, text):
        '''Handle received text from the text channel.

        Converts sender to a Buddy.
        Calls self._activity_cb which is a callback to the activity.
        '''
        _logger.debug('received_cb %r %s' % (type_, text))
        if type_ != 0:
            # Exclude any auxiliary messages
            return

        text = b64decode(text)
        msg = json.loads(text)

        if self._activity_cb:
            try:
                self._text_chan[CHANNEL_INTERFACE_GROUP]
            except Exception:
                # One to one XMPP chat
                nick = self._conn[
                    CONN_INTERFACE_ALIASING].RequestAliases([sender])[0]
                buddy = {'nick': nick, 'color': '#000000,#808080'}
                _logger.error('Exception: recieved from sender %r buddy %r' %
                              (sender, buddy))
            else:
                # XXX: cache these
                buddy = self._get_buddy(sender)
                _logger.error('Else: recieved from sender %r buddy %r' %
                              (sender, buddy))

            self._activity_cb(buddy, msg)
            self._text_chan[
                CHANNEL_TYPE_TEXT].AcknowledgePendingMessages([identity])
        else:
            _logger.debug('Throwing received message on the floor'
                          ' since there is no callback connected. See'
                          ' set_received_callback')

    def set_closed_callback(self, callback):
        '''Connect a callback for when the text channel is closed.

        callback -- callback function taking no args

        '''
        _logger.debug('set closed callback')
        self._activity_close_cb = callback

    def _get_buddy(self, cs_handle):
        '''Get a Buddy from a (possibly channel-specific) handle.'''
        # XXX This will be made redundant once Presence Service
        # provides buddy resolution

        # Get the Presence Service
        pservice = presenceservice.get_instance()

        # Get the Telepathy Connection
        tp_name, tp_path = pservice.get_preferred_connection()
        conn = Connection(tp_name, tp_path)
        group = self._text_chan[CHANNEL_INTERFACE_GROUP]
        my_csh = group.GetSelfHandle()
        if my_csh == cs_handle:
            handle = conn.GetSelfHandle()
        elif group.GetGroupFlags() & \
             CHANNEL_GROUP_FLAG_CHANNEL_SPECIFIC_HANDLES:
            handle = group.GetHandleOwners([cs_handle])[0]
        else:
            handle = cs_handle

            # XXX: deal with failure to get the handle owner
            assert handle != 0

        return pservice.get_buddy_by_telepathy_handle(
            tp_name, tp_path, handle)
