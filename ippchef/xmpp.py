#  -*- coding: utf-8 -*-
# *****************************************************************************
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
#
# Module authors:
#   Alexander Lenz <fslenz@gmail.com>
#
# *****************************************************************************

import time
import threading

import sleekxmpp


class XMPPConnection(sleekxmpp.ClientXMPP, threading.Thread):
    def __init__(self, log, jid, pw, djid):
        threading.Thread.__init__(self)
        sleekxmpp.ClientXMPP.__init__(self, jid, pw)

        self.log = log.getChild('xmpp')
        self._jid = jid
        self._djid = djid
        self._buffer = None

        self.auto_authorize = True
        self.auto_subscribe = True

        self.add_event_handler('session_start', self.init_connection)
        self.add_event_handler('message', self.handle_message)

    def communicate(self, msg, timeout=3.0):
        self.log.debug('Send message to %s: %s' % (self._djid, msg))
        self.send_presence(pto=self._djid)
        self.send_message(mto=self._djid, mbody=msg, mtype='chat')

        for i in range(int(timeout / 0.1)):
            if self._buffer:
                break

            time.sleep(0.1)

        tmp = self._buffer
        self._buffer = None

        return tmp['body']

    def handle_message(self, msg):
        self.log.debug('Got message: %s' % msg)
        self._buffer = msg

    def init_connection(self, event):
        self.log.info('Initialize xmpp connection as %s ...' % self._jid)

        self.log.debug('Send presence ...')
        self.send_presence()

        self.log.debug('Query roster ...')
        self.get_roster()

    def run(self):
        self.log.info('Connect ...')
        self.connect()

        self.log.info('Blocking now ...')
        self.process(block=True)
