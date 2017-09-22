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
import re
import functools
import threading
import pickle
import os.path

from datetime import date, datetime, timedelta, time as dttime

from telegram.ext import Updater, CommandHandler
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from ippchef.xmpp import XMPPConnection


WELCOME_MSG = '''
IPP cantine menu bot.

Commands:
    - /today
    - /tomorrow
    - /subscribe hh:mm
    - /unsubscribe

Inquiries about the bot to: @kuryfox.

'''


class NotificationLoop(threading.Thread):
    def __init__(self, log, bot, loop_delay=15,
                 subscription_file='subscriptions.pickle'):
        threading.Thread.__init__(self)
        self.log = log.getChild('notifier')
        self._bot = bot
        self._loop_delay = loop_delay
        self._sub_chats = {}
        self._subscription_file = subscription_file
        self.restore()

    @property
    def subscriptions(self):
        return self._sub_chats

    def save(self):
        with open(self._subscription_file, 'w') as f:
            pickle.dump(self._sub_chats, f)

    def restore(self):
        if not os.path.isfile(self._subscription_file):
            return
        with open(self._subscription_file, 'r') as f:
            self._sub_chats = pickle.load(f)

    def subscribe_chat(self, chat, timeobj):
        self.unsubscribe_chat(chat)

        self._sub_chats[chat] = (timeobj, None)

        self.save()

    def unsubscribe_chat(self, chat):
        if chat in self._sub_chats:
            del self._sub_chats[chat]

        self.save()

    def run(self):
        self.log.info('Start notification loop')
        while True:
            try:
                today = date.today()

                if today.weekday() <= 4:
                    now = datetime.now().time()
                    for chat, (ntime, last) in self._sub_chats.items():
                        if (not last or last < today) and ntime < now:
                            self._notify(chat)
                            self._sub_chats[chat] = (ntime, today)
                    self.save()
            except Exception as e:
                self.log.exception(e)
            time.sleep(self._loop_delay)

    def _notify(self, chat):
        self.log.debug('Send scheduled notification to: %s ...' % chat)
        self._bot._update_cache()
        self._bot._send(chat, self._bot._cache[1]['today'])


class Bot(object):
    def __init__(self, log, api_key, jid, jpw, djid):
        self.log = log
        self._sub_chats = {}
        self._cache = (None, {})
        self._last_error = None

        self._updater = Updater(api_key)
        self._bot = self._updater.bot

        self._xmpp = XMPPConnection(log, jid, jpw, djid)
        self._xmpp.start()

        time.sleep(2)

        self._notifier = NotificationLoop(self.log, self)
        self._notifier.start()

        self._create_guarded_cmd('start', self.cmd_start)
        self._create_guarded_cmd('today', self.cmd_today)
        self._create_guarded_cmd('tomorrow', self.cmd_tomorrow)
        self._create_guarded_cmd('subscribe', self.cmd_subscribe)
        self._create_guarded_cmd('unsubscribe', self.cmd_unsubscribe)
        self._create_guarded_cmd('disable_keyboard', self.cmd_disable_keyboard)
        self._create_guarded_cmd('debug', self.cmd_debug)

    def run(self):
        self._updater.start_polling()
        self._updater.idle()

    def cmd_start(self, bot, update):
        keyboard = [
            [
                KeyboardButton('/today'),
                KeyboardButton('/tomorrow')
            ]
        ]

        markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

        self._reply(update, WELCOME_MSG, reply_markup=markup)

    def cmd_today(self, bot, update):
        self._update_cache()
        self._reply(update, self._cache[1]['today'])

    def cmd_tomorrow(self, bot, update):
        self._update_cache()
        self._reply(update, self._cache[1]['tomorrow'])

    def cmd_subscribe(self, bot, update):
        msg = re.sub(r'/subscribe(@ippchef_bot)?', '',
                     update.message['text']).strip()

        if not re.match('\d{1,2}:\d\d', msg):
            self._reply(update, 'Invalid time format. Please use '
                                '<pre>/subscribe hh:mm</pre>')
            return

        subtime = dttime(*map(int, msg.split(':')))
        self._notifier.subscribe_chat(update.effective_chat['id'], subtime)

        self._reply(update, 'Subscription successful!')

    def cmd_unsubscribe(self, bot, update):
        self._notifier.unsubscribe_chat(update.effective_chat['id'])
        self._reply(update, 'Unsubscription successful!')

    def cmd_debug(self, bot, update):
        result = ['<b>DEBUG INFO</b>', '']

        result.append('<b>Last error:</b>')
        if self._last_error is None:
            result.append('Nothing happened! Yay!')
        else:
            dt, cmd, user, error = self._last_error
            result.append('%s:\n<i>%s</i> for <i>%s</i>:\n<pre>%s</pre>'
                          % (dt.ctime(), cmd, user, error))

        result.append('')
        result.append('<b>Subscriptions:</b>')

        for chat, (subtime, last) in self._notifier.subscriptions.items():
            result.append('  - %s: %s (last: %s)' % (chat,
                                                     subtime.isoformat(),
                                                     last))

        self._reply(update, '\n'.join(result))

    def cmd_disable_keyboard(self, bot, update):
        self._reply(update, 'Keyboard removed.',
                    reply_markup=ReplyKeyboardRemove())

    def _reformat_menu(self, raw, date):
        menu = ['<b>IPP Menu for %s</b>' % date.strftime('%A %d.%m.%Y'), '']

        for line in raw.splitlines():
            parts = line.split()
            menu.append(' '.join(parts[:-2]))
            menu.append('<i>%s</i>' % ' '.join(parts[-2:]))
            menu.append('')
            pass

        return '\n'.join(menu)

    def _update_cache(self):
        today = date.today()
        if self._cache[0] != today:
            self.log.debug('Cache needs to be updated ...')
            self._cache = (today, {
                'today': self._reformat_menu(
                            self._xmpp.communicate('ipp heute'), today),
                'tomorrow': self._reformat_menu(
                    self._xmpp.communicate('ipp morgen'),
                    today + timedelta(days=1))
            })

    def _reply(self, update, reply, **kwargs):
        update.message.reply_text(reply, parse_mode='HTML', **kwargs)

    def _send(self, chat, msg):
        self.log.debug('Send message to %s: %s', chat, msg)
        self._bot.sendMessage(chat, msg, parse_mode='HTML')

    def _create_guarded_cmd(self, cmd, func):
        func = functools.partial(self._guard_cmd, cmd, func)
        self._updater.dispatcher.add_handler(CommandHandler(cmd, func))

    def _guard_cmd(self, cmd, func, bot, update):
        user = update.effective_user['username'] if update.effective_user \
            else 'Unknown'
        self.log.debug('Exec command "%s" for "%s" ...', cmd, user)
        try:
            return func(bot, update)
        except Exception as e:
            self.log.exception(e)
            self._last_error = (datetime.now(), cmd, user, e)
            self._reply(update, 'Error during command execution:\n\n<pre>%s'
                                '</pre>\n\nPlease message @kuryfox for an '
                                'error report!' % e)
