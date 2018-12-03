from __future__ import absolute_import, unicode_literals

import os
import tempfile
from os import path

from django.conf import settings
from rest_framework import status
from rest_framework.reverse import reverse

from substrabac.celery import app
from substrapp.utils import queryLedger, invokeLedger
from substrapp.utils import get_hash, untar_algo, create_directory, get_remote_file
from substrapp.job_utils import RessourceManager, compute_docker
from substrapp.exception_handler import compute_error_code

import docker
import json
from multiprocessing.managers import BaseManager

import logging


def get_challenge(traintuple):
    from substrapp.models import Challenge

    # check if challenge exists and its metrics is not null
    challengeHash = traintuple['challenge']['hash']

    try:
        # get challenge from local db
        challenge = Challenge.objects.get(pk=challengeHash)
    except:
        challenge = None
    finally:
        if challenge is None or not challenge.metrics:
            # get challenge metrics
            try:
                content, computed_hash = get_remote_file(traintuple['challenge']['metrics'])
            except Exception as e:
                raise e

            challenge, created = Challenge.objects.update_or_create(pkhash=challengeHash, validated=True)

            try:
                f = tempfile.TemporaryFile()
                f.write(content)
                challenge.metrics.save('metrics.py', f)  # update challenge in local db for later use
            except Exception as e:
                logging.error('Failed to save challenge metrics in local db for later use')
                raise e

    return challenge


def get_algo(traintuple):
    algo_content, algo_computed_hash = get_remote_file(traintuple['algo'])
    return algo_content, algo_computed_hash


def get_model(traintuple, model_type):
    model_content, model_computed_hash = None, None

    if traintuple.get(model_type, None) is not None:
        model_content, model_computed_hash = get_remote_file(traintuple[model_type])

    return model_content, model_computed_hash


def put_model(traintuple, traintuple_directory, model_content, model_type):
    if model_content is not None:
        from substrapp.models import Model

        model_dst_path = path.join(traintuple_directory, 'model/model')

        try:
            model = Model.objects.get(pk=traintuple[model_type]['hash'])
        except:  # write it to local disk
            with open(model_dst_path, 'wb') as f:
                f.write(model_content)
        else:
            if get_hash(model.file.path) != traintuple[model_type]['hash']:
                raise Exception('Model Hash in Traintuple is not the same as in local db')

            if not os.path.exists(model_dst_path):
                os.link(model.file.path, model_dst_path)
            else:
                if get_hash(model_dst_path) != traintuple[model_type]['hash']:
                    raise Exception('Model Hash in Traintuple is not the same as in local medias')


def put_opener(traintuple, traintuple_directory, data_type):
    from substrapp.models import Dataset

    try:
        dataset = Dataset.objects.get(pk=traintuple[data_type]['openerHash'])
    except Exception as e:
        raise e

    data_opener_hash = get_hash(dataset.data_opener.path)
    if data_opener_hash != traintuple[data_type]['openerHash']:
        raise Exception('DataOpener Hash in Traintuple is not the same as in local db')

    opener_dst_path = path.join(traintuple_directory, 'opener/%s' % os.path.basename(dataset.data_opener.name))
    if not os.path.exists(opener_dst_path):
        os.link(dataset.data_opener.path, opener_dst_path)


def put_data(traintuple, traintuple_directory, data_type):
    from shutil import copy
    from substrapp.models import Data
    import zipfile

    for data_key in traintuple[data_type]['keys']:
        try:
            data = Data.objects.get(pk=data_key)
        except Exception as e:
            raise e
        else:
            data_hash = get_hash(data.file.path)
            if data_hash != data_key:
                raise Exception('Data Hash in Traintuple is not the same as in local db')

            try:
                to_directory = path.join(traintuple_directory, 'data')
                copy(data.file.path, to_directory)
                # unzip files
                zip_file_path = os.path.join(to_directory, os.path.basename(data.file.name))
                zip_ref = zipfile.ZipFile(zip_file_path, 'r')
                zip_ref.extractall(to_directory)
                zip_ref.close()
                os.remove(zip_file_path)
            except Exception as e:
                logging.error('Fail to unzip data file')
                raise e


def put_metric(traintuple_directory, challenge):
    metrics_dst_path = path.join(traintuple_directory, 'metrics/metrics.py')
    if not os.path.exists(metrics_dst_path):
        os.link(challenge.metrics.path, metrics_dst_path)


def put_algo(traintuple, traintuple_directory, algo_content):
    try:
        untar_algo(algo_content, traintuple_directory, traintuple)
    except Exception as e:
        logging.error('Fail to untar algo file')
        raise e


def build_traintuple_folders(traintuple):
    # create a folder named traintuple['key'] im /medias/traintuple with 5 folders opener, data, model, pred, metrics
    traintuple_directory = path.join(getattr(settings, 'MEDIA_ROOT'), 'traintuple/%s' % traintuple['key'])
    create_directory(traintuple_directory)
    for folder in ['opener', 'data', 'model', 'pred', 'metrics']:
        create_directory(path.join(traintuple_directory, folder))

    return traintuple_directory


def fail(key, err_msg):
    # Log Fail TrainTest
    err_msg = str(err_msg).replace('"', "'").replace('\\', "").replace('\\n', "")[:200]
    data, st = invokeLedger({
        'args': '{"Args":["logFailTrainTest","%(key)s","%(err_msg)s"]}' % {'key': key, 'err_msg': err_msg}
    })

    if st not in (status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED):
        logging.error(data, exc_info=True)

    logging.info('Successfully passed the traintuple to failed')
    return data


# Instatiate Ressource Manager in BaseManager to share it between celery concurrent tasks
BaseManager.register('RessourceManager', RessourceManager)
manager = BaseManager()
manager.start()
ressource_manager = manager.RessourceManager()


def prepareTask(data_type, worker_to_filter, status_to_filter, model_type, status_to_set):
    try:
        data_owner = get_hash(settings.LEDGER['signcert'])
    except Exception as e:
        logging.error(e, exc_info=True)
    else:
        traintuples, st = queryLedger({
            'args': '{"Args":["queryFilter","traintuple~%s~status","%s,%s"]}' % (
                worker_to_filter, data_owner, status_to_filter)
        })

        if st == 200 and traintuples is not None:
            for traintuple in traintuples:

                # get traintuple components
                try:
                    challenge = get_challenge(traintuple)
                    algo_content, algo_computed_hash = get_algo(traintuple)
                    model_content, model_computed_hash = get_model(traintuple, model_type)  # can return None, None
                except Exception as e:
                    error_code = compute_error_code(e)
                    logging.error(error_code, exc_info=True)
                    return fail(traintuple['key'], error_code)

                # create traintuple
                try:
                    traintuple_directory = build_traintuple_folders(traintuple)  # do not put anything in pred folder
                    put_opener(traintuple, traintuple_directory, data_type)
                    put_data(traintuple, traintuple_directory, data_type)
                    put_metric(traintuple_directory, challenge)
                    put_algo(traintuple, traintuple_directory, algo_content)
                    put_model(traintuple, traintuple_directory, model_content, model_type)
                except Exception as e:
                    error_code = compute_error_code(e)
                    logging.error(error_code, exc_info=True)
                    return fail(traintuple['key'], error_code)

                # Log Start TrainTest with status_to_set
                data, st = invokeLedger({
                    'args': '{"Args":["logStartTrainTest","%s","%s"]}' % (traintuple['key'], status_to_set)
                })

                if st not in [status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED]:
                    logging.error('Failed to invoke ledger on prepareTask %s' % data_type)
                else:
                    logging.info('Prepare Task success %s' % data_type)

                    try:
                        doTask.apply_async((traintuple, data_type), queue=settings.LEDGER['org']['name'])
                    except Exception as e:
                        error_code = compute_error_code(e)
                        logging.error(error_code, exc_info=True)
                        return fail(traintuple['key'], error_code)


@app.task
def prepareTrainingTask():
    prepareTask('trainData', 'trainWorker', 'todo', 'startModel', 'training')


@app.task
def prepareTestingTask():
    prepareTask('testData', 'testWorker', 'trained', 'endModel', 'testing')


@app.task
def doTask(traintuple, data_type):
    # Must be defined before to return ressource in case of failure
    cpu_set = None
    gpu_set = None

    try:
        # Log
        job_task_log = ''

        # Setup Docker Client
        client = docker.from_env()

        # traintuple setup
        traintuple_directory = path.join(getattr(settings, 'MEDIA_ROOT'), 'traintuple/%s/' % (traintuple['key']))
        model_path = os.path.join(traintuple_directory, 'model')
        data_path = os.path.join(traintuple_directory, 'data')
        pred_path = os.path.join(traintuple_directory, 'pred')
        opener_file = os.path.join(traintuple_directory, 'opener/opener.py')
        metrics_file = os.path.join(traintuple_directory, 'metrics/metrics.py')
        volumes = {data_path: {'bind': '/sandbox/data', 'mode': 'ro'},
                   pred_path: {'bind': '/sandbox/pred', 'mode': 'rw'},
                   metrics_file: {'bind': '/sandbox/metrics/__init__.py', 'mode': 'ro'},
                   opener_file: {'bind': '/sandbox/opener/__init__.py', 'mode': 'ro'}}

        # compute algo task
        algo_path = path.join(traintuple_directory)
        algo_docker = ('algo_%s' % data_type).lower()  # tag must be lowercase for docker
        algo_docker_name = '%s_%s' % (algo_docker, traintuple['key'])
        model_volume = {model_path: {'bind': '/sandbox/model', 'mode': 'rw'}}
        algo_command = 'train' if data_type == 'trainData' else 'predict' if data_type == 'testData' else None
        job_task_log = compute_docker(client=client,
                                      ressource_manager=ressource_manager,
                                      dockerfile_path=algo_path,
                                      image_name=algo_docker,
                                      container_name=algo_docker_name,
                                      volumes={**volumes, **model_volume},
                                      command=algo_command,
                                      cpu_set=cpu_set,
                                      gpu_set=gpu_set)
        # save model in database
        if data_type == 'trainData':
            from substrapp.models import Model
            end_model_path = path.join(traintuple_directory, 'model/model')
            end_model_file_hash = get_hash(end_model_path)
            instance = Model.objects.create(pkhash=end_model_file_hash, validated=True)
            with open(end_model_path, 'rb') as f:
                instance.file.save('model', f)
            url_http = 'http' if settings.DEBUG else 'https'
            current_site = '%s:%s' % (getattr(settings, 'SITE_HOST'), getattr(settings, 'SITE_PORT'))
            end_model_file = '%s://%s%s' % (url_http, current_site, reverse('substrapp:model-file', args=[end_model_file_hash]))

        # compute metric task
        metrics_path = path.join(getattr(settings, 'PROJECT_ROOT'), 'base_metrics') # base metrics comes with substrabac
        metrics_docker = ('metrics_%s' % data_type).lower()  # tag must be lowercase for docker
        metrics_docker_name = '%s_%s' % (metrics_docker, traintuple['key'])
        metric_volume = {metrics_file: {'bind': '/sandbox/metrics/__init__.py', 'mode': 'ro'}}
        compute_docker(client=client,
                       ressource_manager=ressource_manager,
                       dockerfile_path=metrics_path,
                       image_name=metrics_docker,
                       container_name=metrics_docker_name,
                       volumes={**volumes, **metric_volume},
                       command=None,
                       cpu_set=cpu_set,
                       gpu_set=gpu_set)

        # load performance
        with open(os.path.join(pred_path, 'perf.json'), 'r') as perf_file:
            perf = json.load(perf_file)
        global_perf = perf['all']

    except Exception as e:
        ressource_manager.return_cpu_set(cpu_set)
        ressource_manager.return_gpu_set(gpu_set)
        error_code = compute_error_code(e)
        logging.error(error_code, exc_info=True)
        return fail(traintuple['key'], error_code)

    # Invoke ledger to log success
    if data_type == 'trainData':
        invoke_args = '{"Args":["logSuccessTrain","%s","%s, %s","%s","Train - %s; "]}' % (traintuple['key'],
                                                                                          end_model_file_hash,
                                                                                          end_model_file,
                                                                                          global_perf,
                                                                                          job_task_log)
    elif data_type == 'testData':
        invoke_args = '{"Args":["logSuccessTest","%s","%s","Test - %s; "]}' % (traintuple['key'],
                                                                               global_perf,
                                                                               job_task_log)

    data, st = invokeLedger({
        'args': invoke_args
    })

    if st not in (status.HTTP_201_CREATED, status.HTTP_202_ACCEPTED):
        logging.error('Failed to invoke ledger on logSuccess')
        logging.error(data)
