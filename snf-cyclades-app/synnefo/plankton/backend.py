# Copyright 2011-2013 GRNET S.A. All rights reserved.

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

"""
The Plankton attributes are the following:
  - checksum: the 'hash' meta
  - container_format: stored as a user meta
  - created_at: the 'modified' meta of the first version
  - deleted_at: the timestamp of the last version
  - disk_format: stored as a user meta
  - id: the 'uuid' meta
  - is_public: True if there is a * entry for the read permission
  - location: generated based on the file's path
  - name: stored as a user meta
  - owner: the file's account
  - properties: stored as user meta prefixed with PROPERTY_PREFIX
  - size: the 'bytes' meta
  - status: stored as a system meta
  - store: is always 'pithos'
  - updated_at: the 'modified' meta
"""

import json
import warnings
import logging
import os

from time import time, gmtime, strftime
from functools import wraps
from operator import itemgetter

from django.conf import settings
from django.utils import importlib
from pithos.backends.base import NotAllowedError, VersionNotExists
from synnefo.util.text import uenc


logger = logging.getLogger(__name__)


PLANKTON_DOMAIN = 'plankton'
PLANKTON_PREFIX = 'plankton:'
PROPERTY_PREFIX = 'property:'

PLANKTON_META = ('container_format', 'disk_format', 'name',
                 'status', 'created_at')

MAX_META_KEY_LENGTH = 128 - len(PLANKTON_DOMAIN) - len(PROPERTY_PREFIX)
MAX_META_VALUE_LENGTH = 256

from pithos.backends.util import PithosBackendPool
_pithos_backend_pool = \
    PithosBackendPool(
        settings.PITHOS_BACKEND_POOL_SIZE,
        astakos_auth_url=settings.ASTAKOS_AUTH_URL,
        service_token=settings.CYCLADES_SERVICE_TOKEN,
        astakosclient_poolsize=settings.CYCLADES_ASTAKOSCLIENT_POOLSIZE,
        db_connection=settings.BACKEND_DB_CONNECTION,
        block_path=settings.BACKEND_BLOCK_PATH)


def get_pithos_backend():
    return _pithos_backend_pool.pool_get()


def create_url(account, container, name):
    assert "/" not in account, "Invalid account"
    assert "/" not in container, "Invalid container"
    return "pithos://%s/%s/%s" % (account, container, name)


def split_url(url):
    """Returns (accout, container, object) from a url string"""
    try:
        assert(isinstance(url, basestring))
        t = url.split('/', 4)
        assert t[0] == "pithos:", "Invalid url"
        assert len(t) == 5, "Invalid url"
        return t[2:5]
    except AssertionError:
        raise InvalidLocation("Invalid location '%s" % url)


def format_timestamp(t):
    return strftime('%Y-%m-%d %H:%M:%S', gmtime(t))


def handle_backend_exceptions(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except NotAllowedError:
            raise Forbidden
        except NameError:
            raise ImageNotFound
        except VersionNotExists:
            raise ImageNotFound
    return wrapper


def commit_on_success(func):
    def wrapper(self, *args, **kwargs):
        backend = self.backend
        backend.pre_exec()
        try:
            ret = func(self, *args, **kwargs)
        except:
            backend.post_exec(False)
            raise
        else:
            backend.post_exec(True)
        return ret
    return wrapper


class ImageBackend(object):
    """A wrapper arround the pithos backend to simplify image handling."""

    def __init__(self, user):
        self.user = user

        original_filters = warnings.filters
        warnings.simplefilter('ignore')         # Suppress SQLAlchemy warnings
        self.backend = get_pithos_backend()
        warnings.filters = original_filters     # Restore warnings

    def close(self):
        """Close PithosBackend(return to pool)"""
        self.backend.close()

    @handle_backend_exceptions
    @commit_on_success
    def get_image(self, image_uuid):
        """Retrieve information about an image."""
        image_url = self._get_image_url(image_uuid)
        return self._get_image(image_url)

    def _get_image_url(self, image_uuid):
        """Get the Pithos url that corresponds to an image UUID."""
        account, container, name = self.backend.get_uuid(self.user, image_uuid)
        return create_url(account, container, name)

    def _get_image(self, image_url):
        """Get information about an Image.

        Get all available information about an Image.
        """
        account, container, name = split_url(image_url)
        try:
            meta = self._get_meta(image_url)
            meta["deleted"] = ""
        except NameError:
            versions = self.backend.list_versions(self.user, account,
                                                  container, name)
            if not versions:
                raise Exception("Image without versions %s" % image_url)
            # Object was deleted, use the latest version
            version, timestamp = versions[-1]
            meta = self._get_meta(image_url, version)
            meta["deleted"] = timestamp

        # XXX: Check that an object is a plankton image! PithosBackend will
        # return common metadata for an object, even if it has no metadata in
        # plankton domain. All images must have a name, so we check if a file
        # is an image by checking if they are having an image name.
        if PLANKTON_PREFIX + 'name' not in meta:
            raise ImageNotFound

        permissions = self._get_permissions(image_url)
        return image_to_dict(image_url, meta, permissions)

    def _get_meta(self, image_url, version=None):
        """Get object's metadata."""
        account, container, name = split_url(image_url)
        return self.backend.get_object_meta(self.user, account, container,
                                            name, PLANKTON_DOMAIN, version)

    def _update_meta(self, image_url, meta, replace=False):
        """Update object's metadata."""
        account, container, name = split_url(image_url)

        prefixed = [(PLANKTON_PREFIX + uenc(k), uenc(v))
                    for k, v in meta.items()
                    if k in PLANKTON_META or k.startswith(PROPERTY_PREFIX)]
        prefixed = dict(prefixed)

        for k, v in prefixed.items():
            if len(k) > 128:
                raise InvalidMetadata('Metadata keys should be less than %s '
                                      'characters' % MAX_META_KEY_LENGTH)
            if len(v) > 256:
                raise InvalidMetadata('Metadata values should be less than %s '
                                      'characters.' % MAX_META_VALUE_LENGTH)

        self.backend.update_object_meta(self.user, account, container, name,
                                        PLANKTON_DOMAIN, prefixed, replace)
        logger.debug("User '%s' updated image '%s', meta: '%s'", self.user,
                     image_url, prefixed)

    def _get_permissions(self, image_url):
        """Get object's permissions."""
        account, container, name = split_url(image_url)
        _a, path, permissions = \
            self.backend.get_object_permissions(self.user, account, container,
                                                name)

        if path is None and permissions != {}:
            logger.warning("Image '%s' got permissions '%s' from 'None' path.",
                           image_url, permissions)
            raise Exception("Database Inconsistency Error:"
                            " Image '%s' got permissions from 'None' path." %
                            image_url)

        return permissions

    def _update_permissions(self, image_url, permissions):
        """Update object's permissions."""
        account, container, name = split_url(image_url)
        self.backend.update_object_permissions(self.user, account, container,
                                               name, permissions)
        logger.debug("User '%s' updated image '%s', permissions: '%s'",
                     self.user, image_url, permissions)

    @handle_backend_exceptions
    @commit_on_success
    def unregister(self, image_uuid):
        """Unregister an image.

        Unregister an image, by removing all metadata from the Pithos
        file that exist in the PLANKTON_DOMAIN.

        """
        image_url = self._get_image_url(image_uuid)
        self._get_image(image_url)  # Assert that it is an image
        # Unregister the image by removing all metadata from domain
        # 'PLANKTON_DOMAIN'
        meta = {}
        self._update_meta(image_url, meta, True)
        logger.debug("User '%s' deleted image '%s'", self.user, image_url)

    @handle_backend_exceptions
    @commit_on_success
    def add_user(self, image_uuid, add_user):
        """Add a user as an image member.

        Update read permissions of Pithos file, to include the specified user.

        """
        image_url = self._get_image_url(image_uuid)
        self._get_image(image_url)  # Assert that it is an image
        permissions = self._get_permissions(image_url)
        read = set(permissions.get("read", []))
        assert(isinstance(add_user, (str, unicode)))
        read.add(add_user)
        permissions["read"] = list(read)
        self._update_permissions(image_url, permissions)

    @handle_backend_exceptions
    @commit_on_success
    def remove_user(self, image_uuid, remove_user):
        """Remove the user from image members.

        Remove the specified user from the read permissions of the Pithos file.

        """
        image_url = self._get_image_url(image_uuid)
        self._get_image(image_url)  # Assert that it is an image
        permissions = self._get_permissions(image_url)
        read = set(permissions.get("read", []))
        assert(isinstance(remove_user, (str, unicode)))
        try:
            read.remove(remove_user)
        except ValueError:
            return  # TODO: User did not have access
        permissions["read"] = list(read)
        self._update_permissions(image_url, permissions)

    @handle_backend_exceptions
    @commit_on_success
    def replace_users(self, image_uuid, replace_users):
        """Replace image members.

        Replace the read permissions of the Pithos files with the specified
        users. If image is specified as public, we must preserve * permission.

        """
        image_url = self._get_image_url(image_uuid)
        image = self._get_image(image_url)
        permissions = self._get_permissions(image_url)
        assert(isinstance(replace_users, list))
        permissions["read"] = replace_users
        if image.get("is_public", False):
            permissions["read"].append("*")
        self._update_permissions(image_url, permissions)

    @handle_backend_exceptions
    @commit_on_success
    def list_users(self, image_uuid):
        """List the image members.

        List the image members, by listing all users that have read permission
        to the corresponding Pithos file.

        """
        image_url = self._get_image_url(image_uuid)
        self._get_image(image_url)  # Assert that it is an image
        permissions = self._get_permissions(image_url)
        return [user for user in permissions.get('read', []) if user != '*']

    @handle_backend_exceptions
    @commit_on_success
    def update_metadata(self, image_uuid, metadata):
        """Update Image metadata."""
        image_url = self._get_image_url(image_uuid)
        self._get_image(image_url)  # Assert that it is an image

        # 'is_public' metadata is translated in proper file permissions
        is_public = metadata.pop("is_public", None)
        if is_public is not None:
            permissions = self._get_permissions(image_url)
            read = set(permissions.get("read", []))
            if is_public:
                read.add("*")
            else:
                read.discard("*")
            permissions["read"] = list(read)
            self._update_permissions(image_url, permissions)

        # Extract the properties dictionary from metadata, and store each
        # property as a separeted, prefixed metadata
        properties = metadata.pop("properties", {})
        meta = dict([(PROPERTY_PREFIX + k, v) for k, v in properties.items()])
        # Also add the following metadata
        meta.update(**metadata)

        self._update_meta(image_url, meta)
        image_url = self._get_image_url(image_uuid)
        return self._get_image(image_url)

    @handle_backend_exceptions
    @commit_on_success
    def register(self, name, image_url, metadata):
        # Validate that metadata are allowed
        if "id" in metadata:
            raise ValueError("Passing an ID is not supported")
        store = metadata.pop("store", "pithos")
        if store != "pithos":
            raise ValueError("Invalid store '%s'. Only 'pithos' store is"
                             "supported" % store)
        disk_format = metadata.setdefault("disk_format",
                                          settings.DEFAULT_DISK_FORMAT)
        if disk_format not in settings.ALLOWED_DISK_FORMATS:
            raise ValueError("Invalid disk format '%s'" % disk_format)
        container_format =\
            metadata.setdefault("container_format",
                                settings.DEFAULT_CONTAINER_FORMAT)
        if container_format not in settings.ALLOWED_CONTAINER_FORMATS:
            raise ValueError("Invalid container format '%s'" %
                             container_format)

        # Validate that 'size' and 'checksum' are valid
        account, container, object = split_url(image_url)

        meta = self._get_meta(image_url)

        size = int(metadata.pop('size', meta['bytes']))
        if size != meta['bytes']:
            raise ValueError("Invalid size")

        checksum = metadata.pop('checksum', meta['hash'])
        if checksum != meta['hash']:
            raise ValueError("Invalid checksum")

        # Fix permissions
        is_public = metadata.pop('is_public', False)
        if is_public:
            permissions = {'read': ['*']}
        else:
            permissions = {'read': [self.user]}

        # Extract the properties dictionary from metadata, and store each
        # property as a separeted, prefixed metadata
        properties = metadata.pop("properties", {})
        meta = dict([(PROPERTY_PREFIX + k, v) for k, v in properties.items()])
        # Add creation(register) timestamp as a metadata, to avoid extra
        # queries when retrieving the list of images.
        meta['created_at'] = time()
        # Update rest metadata
        meta.update(name=name, status='available', **metadata)

        # Do the actualy update in the Pithos backend
        self._update_meta(image_url, meta)
        self._update_permissions(image_url, permissions)
        logger.debug("User '%s' created image '%s'('%s')", self.user,
                     image_url, uenc(name))
        return self._get_image(image_url)

    def _list_images(self, user=None, filters=None, params=None):
        filters = filters or {}

        # TODO: Use filters
        # # Fix keys
        # keys = [PLANKTON_PREFIX + 'name']
        # size_range = (None, None)
        # for key, val in filters.items():
        #     if key == 'size_min':
        #         size_range = (val, size_range[1])
        #     elif key == 'size_max':
        #         size_range = (size_range[0], val)
        #     else:
        #         keys.append('%s = %s' % (PLANKTON_PREFIX + key, val))
        _images = self.backend.get_domain_objects(domain=PLANKTON_DOMAIN,
                                                  user=user)

        images = []
        for (location, meta, permissions) in _images:
            image_url = "pithos://" + location
            meta["modified"] = meta["version_timestamp"]
            images.append(image_to_dict(image_url, meta, permissions))

        if params is None:
            params = {}
        key = itemgetter(params.get('sort_key', 'created_at'))
        reverse = params.get('sort_dir', 'desc') == 'desc'
        images.sort(key=key, reverse=reverse)
        return images

    @commit_on_success
    def list_images(self, filters=None, params=None):
        return self._list_images(user=self.user, filters=filters,
                                 params=params)

    @commit_on_success
    def list_shared_images(self, member, filters=None, params=None):
        images = self._list_images(user=self.user, filters=filters,
                                   params=params)
        is_shared = lambda img: not img["is_public"] and img["owner"] == member
        return filter(is_shared, images)

    @commit_on_success
    def list_public_images(self, filters=None, params=None):
        images = self._list_images(user=None, filters=filters, params=params)
        return filter(lambda img: img["is_public"], images)


class ImageBackendError(Exception):
    pass


class ImageNotFound(ImageBackendError):
    pass


class Forbidden(ImageBackendError):
    pass


class InvalidMetadata(ImageBackendError):
    pass


class InvalidLocation(ImageBackendError):
    pass


def image_to_dict(image_url, meta, permissions):
    """Render an image to a dictionary"""
    account, container, name = split_url(image_url)

    image = {}
    if PLANKTON_PREFIX + 'name' not in meta:
        logger.warning("Image without Plankton name!! url %s meta %s",
                       image_url, meta)
        image[PLANKTON_PREFIX + "name"] = ""

    image["id"] = meta["uuid"]
    image["location"] = image_url
    image["checksum"] = meta["hash"]
    created = meta.get("created_at", meta["modified"])
    image["created_at"] = format_timestamp(created)
    deleted = meta.get("deleted", None)
    image["deleted_at"] = format_timestamp(deleted) if deleted else ""
    image["updated_at"] = format_timestamp(meta["modified"])
    image["size"] = meta["bytes"]
    image["store"] = "pithos"
    image['owner'] = account

    # Permissions
    image["is_public"] = "*" in permissions.get('read', [])

    properties = {}
    for key, val in meta.items():
        # Get plankton properties
        if key.startswith(PLANKTON_PREFIX):
            # Remove plankton prefix
            key = key.replace(PLANKTON_PREFIX, "")
            # Keep only those in plankton meta
            if key in PLANKTON_META:
                if key != "created_at":
                    # created timestamp is return in 'created_at' field
                    image[key] = val
            elif key.startswith(PROPERTY_PREFIX):
                key = key.replace(PROPERTY_PREFIX, "")
                properties[key] = val
    image["properties"] = properties

    return image


class JSONFileBackend(object):
    """
    A dummy image backend that loads available images from a file with json
    formatted content.

    usage:
        PLANKTON_BACKEND_MODULE = 'synnefo.plankton.backend.JSONFileBackend'
        PLANKTON_IMAGES_JSON_BACKEND_FILE = '/tmp/images.json'

        # loading images from an existing plankton service
        $ curl -H "X-Auth-Token: <MYTOKEN>" \
                https://cyclades.synnefo.org/plankton/images/detail | \
                python -m json.tool > /tmp/images.json
    """
    def __init__(self, userid):
        self.images_file = getattr(settings,
                                   'PLANKTON_IMAGES_JSON_BACKEND_FILE', '')
        if not os.path.exists(self.images_file):
            raise Exception("Invalid plankgon images json backend file: %s",
                            self.images_file)
        fp = file(self.images_file)
        self.images = json.load(fp)
        fp.close()

    def iter(self, *args, **kwargs):
        return self.images.__iter__()

    def list_images(self, *args, **kwargs):
        return self.images

    def get_image(self, image_uuid):
        try:
            return filter(lambda i: i['id'] == image_uuid, self.images)[0]
        except IndexError:
            raise Exception("Unknown image uuid: %s" % image_uuid)

    def close(self):
        pass


def get_backend():
    backend_module = getattr(settings, 'PLANKTON_BACKEND_MODULE', None)
    if not backend_module:
        # no setting set
        return ImageBackend

    parts = backend_module.split(".")
    module = ".".join(parts[:-1])
    cls = parts[-1]
    try:
        return getattr(importlib.import_module(module), cls)
    except (ImportError, AttributeError), e:
        raise ImportError("Cannot import plankton module: %s (%s)" %
                          (backend_module, e.message))
