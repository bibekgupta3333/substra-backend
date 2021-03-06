import os
import shutil
import tempfile
import json

import mock

from django.urls import reverse
from django.test import override_settings

from rest_framework import status
from rest_framework.test import APITestCase

from ..common import get_sample_datamanager, AuthenticatedClient

MEDIA_ROOT = tempfile.mkdtemp()


@override_settings(MEDIA_ROOT=MEDIA_ROOT)
@override_settings(DEFAULT_DOMAIN='http://testserver')
class DataManagerQueryTests(APITestCase):
    client_class = AuthenticatedClient

    def setUp(self):
        if not os.path.exists(MEDIA_ROOT):
            os.makedirs(MEDIA_ROOT)

        self.data_description, self.data_description_filename, self.data_data_opener, \
            self.data_opener_filename = get_sample_datamanager()

    def tearDown(self):
        shutil.rmtree(MEDIA_ROOT, ignore_errors=True)

    def get_default_datamanager_data(self):
        return {
            'json': json.dumps({
                'name': 'slide opener',
                'type': 'images',
                'permissions': {
                    'public': True,
                    'authorized_ids': [],
                },
                'objective_key': None,
            }),
            'description': self.data_description,
            'data_opener': self.data_data_opener
        }

    def test_add_datamanager_sync_ok(self):

        data = self.get_default_datamanager_data()

        url = reverse('substrapp:data_manager-list')
        extra = {
            'HTTP_SUBSTRA_CHANNEL_NAME': 'mychannel',
            'HTTP_ACCEPT': 'application/json;version=0.0',
        }

        with mock.patch('substrapp.ledger.assets.invoke_ledger') as minvoke_ledger:
            minvoke_ledger.return_value = {'key': 'some key'}

            response = self.client.post(url, data, format='multipart', **extra)
            r = response.json()
            self.assertIsNotNone(r['key'])
            self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    @override_settings(LEDGER_SYNC_ENABLED=False)
    @override_settings(
        task_eager_propagates=True,
        task_always_eager=True,
        broker_url='memory://',
        backend='memory'
    )
    def test_add_datamanager_no_sync_ok(self):

        data = self.get_default_datamanager_data()

        url = reverse('substrapp:data_manager-list')
        extra = {
            'HTTP_SUBSTRA_CHANNEL_NAME': 'mychannel',
            'HTTP_ACCEPT': 'application/json;version=0.0',
        }

        with mock.patch('substrapp.ledger.assets.invoke_ledger') as minvoke_ledger:
            minvoke_ledger.return_value = {
                'message': 'DataManager added in local db waiting for validation.'
                           'The substra network has been notified for adding this DataManager'
            }
            response = self.client.post(url, data, format='multipart', **extra)
            r = response.json()

            self.assertIsNotNone(r['key'])
            self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)

    def test_add_datamanager_ko(self):
        data = {'name': 'toto'}

        url = reverse('substrapp:data_manager-list')
        extra = {
            'HTTP_SUBSTRA_CHANNEL_NAME': 'mychannel',
            'HTTP_ACCEPT': 'application/json;version=0.0',
        }
        response = self.client.post(url, data, format='multipart', **extra)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
