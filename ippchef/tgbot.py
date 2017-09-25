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
import threading
import pickle
import os.path

from datetime import date, datetime, timedelta, time as dttime

from telegram.ext import Updater, CommandHandler
from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove

from ippchef.xmpp import XMPPConnection
from ippchef.util import tg_reply, check_admin_rights


WELCOME_MSG = '''
IPP cantine menu bot.

Commands:
    - /today - Show today's menu
    - /tomorrow - Show tomorrow's menu
    - /subscribe hh:mm - Subscribe to daily notifications
    - /unsubscribe - Unsubscribe from daily notifications
    - /show_subscription - Show your subscription

    - /disable_keyboard - If you don't like keyboards

Inquiries about the bot to: @kuryfox.


<b>PLEASE NOTE: THIS BOT IS CURRENTLY WORK IN PROGRESS AND __NOT__ STABLE YET</b>
'''


ADMIN_USERS = ['@kuryfox', '@farrowstrange']


class GuardedCommandHandler(CommandHandler):
    def __init__(self, log, command, callback, needs_admin=False):
        CommandHandler.__init__(self, command, callback)
        self.log = log.getChild(command)
        self._needs_admin = needs_admin

    def handle_update(self, update, dispatcher):
        user = update.effective_user
        self.log.debug('Exec command for %s (%s) ...', user.id, user.name)

        try:
            if self._needs_admin:
                check_admin_rights(update.effective_user.name.lower())

            return CommandHandler.handle_update(self, update, dispatcher)
        except Exception as e:
            self.log.exception(e)
            tg_reply(update,'Error during command execution:\n\n<pre>%s</pre>'
                            '\n\nPlease message @kuryfox for an error report!'
                     % e, self.log)


class NotificationLoop(threading.Thread):
    def __init__(self, log, bot, loop_delay=15,
                 subscription_file='subscriptions.pickle'):
        threading.Thread.__init__(self)
        self.log = log.getChild('notifier')
        self._bot = bot
        self._loop_delay = loop_delay
        self._sub_chats = {}
        self._subscription_file = subscription_file
        self._file_lock = threading.Lock()
        self.restore()

    @property
    def subscriptions(self):
        return self._sub_chats

    def save(self):
        with self._file_lock:
            with open(self._subscription_file, 'w') as f:
                pickle.dump(self._sub_chats, f)

    def restore(self):
        with self._file_lock:
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

    def get_chat_subscription(self, chat):
        return self._sub_chats.get(chat, None)

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

        self._updater = Updater(api_key)
        self._bot = self._updater.bot
        self._xmpp = XMPPConnection(log, jid, jpw, djid)
        self._notifier = NotificationLoop(self.log, self)

        self._create_command('start', self.cmd_start)
        self._create_command('today', self.cmd_today)
        self._create_command('tomorrow', self.cmd_tomorrow)
        self._create_command('subscribe', self.cmd_subscribe)
        self._create_command('unsubscribe', self.cmd_unsubscribe)
        self._create_command('show_subscription', self.cmd_show_subscription)
        self._create_command('disable_keyboard', self.cmd_disable_keyboard)
        self._create_command('debug', self.cmd_debug, True)
        self._create_command('refresh_cache', self.cmd_refresh_cache, True)

    def run(self):
        self._xmpp.start()
        self.log.debug('Wait for the xmpp connection to get ready ...')
        time.sleep(3.0)
        self._update_cache()

        self._notifier.start()

        self.log.info('Enter telegram bot loop')
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

        self._reply(update, WELCOME_MSG, self.log, reply_markup=markup)

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

    def cmd_show_subscription(self, bot, update):
        sub = self._notifier.get_chat_subscription(update.effective_chat['id'])

        if sub is None:
            self._reply(update, 'You didn\'t subscribe yet. Use '
                        '<pre>/subscribe hh:mm</pre> to subscribe.)')
        else:
            self._reply(update, '<b>Active subscription:</b> %s (last notification: '
                                '%s)'
                        % (sub[0], sub[1]))

    def cmd_debug(self, bot, update):
        result = ['<b>DEBUG INFO</b>', '']

        result.append('<b>Subscriptions:</b>')
        for chat, (subtime, last) in self._notifier.subscriptions.items():
            result.append('  - %s: %s (last: %s)' % (chat,
                                                     subtime.isoformat(),
                                                     last))

        self._reply(update, '\n'.join(result))

    def cmd_refresh_cache(self, bot, update):
        self._cache = (None, None)
        self._update_cache()
        self._reply(update, 'Done')

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

    def _send(self, chat, msg):
        self.log.debug('Send message to %s: %s', chat, msg)
        self._bot.sendMessage(chat, msg, parse_mode='HTML')

    def _reply(self, update, message, **kwargs):
        tg_reply(update, message, self.log, **kwargs)

    def _create_command(self, cmd, func, admin=False):
        self._updater.dispatcher.add_handler(
            GuardedCommandHandler(self.log, cmd, func, admin))
