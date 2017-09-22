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

import datetime
import functools

from telegram.ext import Updater, CommandHandler
from telegram import KeyboardButton, ReplyKeyboardMarkup

from ippchef.xmpp import XMPPConnection


WELCOME_MSG = '''
IPP cantine menu bot.

Commands:
    - /today
    - /tomorrow

Inquiries about the bot at @kuryfox.

'''


class Bot(object):
    def __init__(self, log, api_key, jid, jpw, djid):
        self.log = log
        self._sub_times = {}
        self._sub_chats = {}
        self._cache = (None, {})
        self._last_error = None

        self._xmpp = XMPPConnection(log, jid, jpw, djid)
        self._xmpp.start()

        self._updater = Updater(api_key)
        self._bot = self._updater.bot

        self._create_guarded_cmd('start', self.cmd_start)
        self._create_guarded_cmd('today', self.cmd_today)
        self._create_guarded_cmd('tomorrow', self.cmd_tomorrow)
        self._create_guarded_cmd('subscribe', self.cmd_subscribe)
        self._create_guarded_cmd('unsubscribe', self.cmd_unsubscribe)
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
        msg = update.message['text'][10:].strip()

        print(update.effective_chat)

        raise NotImplementedError('Command not implemented yet')

    def cmd_unsubscribe(self, bot, update):

        uid = update.effective_user['id']

        raise NotImplementedError('Command not implemented yet')

    def cmd_debug(self, bot, update):
        result = ['<b>DEBUG INFO</b>', '']

        result.append('<b>Last error:</b>')
        if self._last_error is None:
            result.append('Nothing happened! Yay!')
        else:
            dt, cmd, user, error = self._last_error
            result.append('%s:\n<i>%s</i> for <i>%s</i>:\n<pre>%s</pre>'
                          % (dt.ctime(), cmd, user, error))

        self._reply(update, '\n'.join(result))

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
        today = datetime.date.today()
        if self._cache[0] != today:
            self.log.debug('Cache needs to be updated ...')
            self._cache = (today, {
                'today': self._reformat_menu(
                            self._xmpp.communicate('ipp heute'), today),
                'tomorrow': self._reformat_menu(
                    self._xmpp.communicate('ipp morgen'),
                    today + datetime.timedelta(days=1))
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
            self._last_error = (datetime.datetime.now(), cmd, user, e)
            self._reply(update, 'Error during command execution, please take a '
                                'look at the log files for further '
                                'information.')
