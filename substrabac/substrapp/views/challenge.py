from django.db import IntegrityError
from django.http import Http404
from rest_framework import status, mixins
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet

from substrapp.conf import conf
from substrapp.models import Challenge
from substrapp.serializers import ChallengeSerializer, LedgerChallengeSerializer

# from hfc.fabric import Client
# cli = Client(net_profile="../network.json")
from substrapp.utils import queryLedger


class ChallengeViewSet(mixins.CreateModelMixin,
                       mixins.ListModelMixin,
                       GenericViewSet):
    queryset = Challenge.objects.all()
    serializer_class = ChallengeSerializer

    # permission_classes = (permissions.IsAuthenticated,)

    def perform_create(self, serializer):
        return serializer.save()

    def create(self, request, *args, **kwargs):
        """
        Create a new Challenge \n
            TODO add info about what has to be posted\n
        - Example with curl (on localhost): \n
            curl -u username:password -H "Content-Type: application/json"\
            -X POST\
            -d '{"name": "tough challenge", "permissions": "all", "metrics_name": 'accuracy', "test_data":
            ["data_5c1d9cd1c2c1082dde0921b56d11030c81f62fbb51932758b58ac2569dd0b379",
            "data_5c1d9cd1c2c1082dde0921b56d11030c81f62fbb51932758b58ac2569dd0b389"],\
                "files": {"description.md": '#My tough challenge',\
                'metrics.py': 'def AUC_score(y_true, y_pred):\n\treturn 1'}}'\
                http://127.0.0.1:8000/substrapp/challenge/ \n
            Use double quotes for the json, simple quotes don't work.\n
        - Example with the python package requests (on localhost): \n
            requests.post('http://127.0.0.1:8000/challenge/',
                          #auth=('username', 'password'),
                          data={'name': 'MSI classification', 'permissions': 'all', 'metrics_name': 'accuracy', 'test_data_keys': ['data_da1bb7c31f62244c0f3a761cc168804227115793d01c270021fe3f7935482dcc']},
                          files={'description': open('description.md', 'rb'), 'metrics': open('metrics.py', 'rb')},
                          headers={'Accept': 'application/json;version=0.0'}) \n
        ---
        response_serializer: ChallengeSerializer
        """

        data = request.data
        serializer = self.get_serializer(data={'metrics': data.get('metrics'),
                                               'description': data.get('description')})

        serializer.is_valid(raise_exception=True)

        # create on db
        try:
            instance = self.perform_create(serializer)
        except IntegrityError as exc:
            return Response({'message': 'A challenge with this description file already exists.'},
                            status=status.HTTP_409_CONFLICT)
        else:
            # init ledger serializer
            ledger_serializer = LedgerChallengeSerializer(data={'test_data_keys': data.getlist('test_data_keys'),
                                                                'name': data.get('name'),
                                                                'permissions': data.get('permissions'),
                                                                'metrics_name': data.get('metrics_name'),
                                                                'instance': instance},
                                                          context={'request': request})

            if not ledger_serializer.is_valid():
                # delete instance
                instance.delete()
                raise ValidationError(ledger_serializer.errors)

            # create on ledger
            data = ledger_serializer.create(ledger_serializer.validated_data)

            st = status.HTTP_201_CREATED
            headers = self.get_success_headers(serializer.data)

            data.update(serializer.data)
            return Response(data, status=st, headers=headers)

    def list(self, request, *args, **kwargs):

        # can modify result by interrogating `request.version`

        # using chu-nantes as in our testing owkin has been revoked
        org = conf['orgs']['chu-nantes']
        peer = org['peers'][0]

        data, st = queryLedger({
            'org': org,
            'peer': peer,
            'args': '{"Args":["queryChallenges"]}'
        })

        return Response(data, status=st)

    @action(detail=True)
    def metrics(self, request, *args, **kwargs):
        instance = self.get_object()

        # TODO fetch challenge from ledger
        # if requester has permission, return metrics

        serializer = self.get_serializer(instance)
        return Response(serializer.data['metrics'])

    @action(detail=True)
    def leaderboard(self, request, *args, **kwargs):

        # using chu-nantes as in our testing owkin has been revoked
        org = conf['orgs']['chu-nantes']
        peer = org['peers'][0]

        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        pk = self.kwargs[lookup_url_kwarg]

        try:
            # try to get it from local db
            instance = self.get_object()
        except Http404:
            # get instance from remote node
            challenge, st = queryLedger({
                'org': org,
                'peer': peer,
                'args': '{"Args":["queryObject","' + pk + '"]}'
            })

            # TODO check hash

            # TODO save challenge in local db for later use
            # instance = Challenge.objects.create(description=challenge['description'], metrics=challenge['metrics'])
        finally:
            # TODO query list of algos and models from ledger
            algos, _ = self.queryLedger({
                'org': org,
                'peer': peer,
                'args': '{"Args":["queryObjects", "algo"]}'
            })
            models, _ = self.queryLedger({
                'org': org,
                'peer': peer,
                'args': '{"Args":["queryObjects", "model"]}'
            })
            # TODO sort algos given the best perfs of their models

            # TODO return success, challenge info, sorted algo + models

            # serializer = self.get_serializer(instance)
            return Response({
                'challenge': challenge,
                'algos': [x for x in algos if x['challenge'] == pk],
                'models': models
            })

    @action(detail=True)
    def data(self, request, *args, **kwargs):
        instance = self.get_object()

        # TODO fetch list of data from ledger
        # query list of related algos and models from ledger

        # return success and model

        serializer = self.get_serializer(instance)
        return Response(serializer.data)
