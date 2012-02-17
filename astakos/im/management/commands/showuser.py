# Copyright 2011 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
#
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

from datetime import datetime
from optparse import make_option

from django.core.management.base import BaseCommand, CommandError
from django.utils.timesince import timesince, timeuntil

from astakos.im.models import AstakosUser


def format_bool(b):
    return 'YES' if b else 'NO'

def format_date(d):
    if d < datetime.now():
        return timesince(d) + ' ago'
    else:
        return 'in ' + timeuntil(d)


class Command(BaseCommand):
    help = "Show user info"
    
    def handle(self, *args, **options):
        if len(args) != 1:
            raise CommandError("Please provide a user_id or email")
        
        email_or_id = args[0]
        try:
            if email_or_id.isdigit():
                user = AstakosUser.objects.get(id=int(email_or_id))
            else:
                user = AstakosUser.objects.get(email=email_or_id)
        except AstakosUser.DoesNotExist:
            field = 'id' if email_or_id.isdigit() else 'email'
            msg = "Unknown user with %s '%s'" % (field, email_or_id)
            raise CommandError(msg)
        
        kv = {
            'id': user.id,
            'email': user.email,
            'first name': user.first_name,
            'last name': user.last_name,
            'active': format_bool(user.is_active),
            'admin': format_bool(user.is_superuser),
            'last login': format_date(user.last_login),
            'date joined': format_date(user.date_joined),
            'last update': format_date(user.updated),
            'token': user.auth_token,
            'token expiration': format_date(user.auth_token_expires),
            'invitations': user.invitations,
            'invitation level': user.level,
            'provider': user.provider,
            'verified': format_bool(user.is_verified)
        }
        
        for key, val in sorted(kv.items()):
            line = '%s: %s\n' % (key.rjust(16), val)
            self.stdout.write(line.encode('utf8'))
