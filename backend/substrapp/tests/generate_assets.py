import os
import json
import substra


dir_path = os.path.dirname(__file__)
assets_path = os.path.join(dir_path, 'assets.py')


def main():

    client = substra.Client(url='http://substra-backend.node-1.com',
                            insecure=False)
    client.login('node-1', 'p@$swr0d44')

    assets = {}
    assets['objective'] = json.dumps(client.list_objective(), indent=4)
    assets['datamanager'] = json.dumps(client.list_dataset(), indent=4)
    assets['algo'] = json.dumps(client.list_algo(), indent=4)
    assets['traintuple'] = json.dumps(client.list_traintuple(), indent=4)
    assets['testtuple'] = json.dumps(client.list_testtuple(), indent=4)
    assets['computeplan'] = json.dumps(client.list_compute_plan(), indent=4)
    assets['compositetraintuple'] = json.dumps(client.list_composite_traintuple(), indent=4)
    assets['compositealgo'] = json.dumps(client.list_composite_algo(), indent=4)

    models = client._backend.list(substra.sdk.schemas.Type.Model)
    models = [(model, list(model.keys()).pop()) for model in models]
    assets['model'] = json.dumps([client._backend.get(substra.sdk.schemas.Type.Model, model[mtype]['key'])
                                  for model, mtype in models], indent=4)

    with open(assets_path, 'w') as f:
        f.write('"""\nWARNING\n=======\n\nDO NOT MANUALLY EDIT THIS FILE!\n\n'
                'It is generated using substrapp/tests/generate_assets.py\n\n'
                'In order to update this file:\n'
                '1. start a clean instance of substra\n'
                '2. run computation on it (with e2e tests for instance)\n'
                '3. run substrapp/tests/generate_assets.py\n"""\n\n')
        for k, v in assets.items():
            v = v.replace('substra-backend.node-1.com', 'testserver')
            v = v.replace('substra-backend.node-2.com', 'testserver')
            v = v.replace('true', 'True')
            v = v.replace('false', 'False')
            v = v.replace('null', 'None')
            f.write(f'{k} = {v}')
            f.write('\n\n')


if __name__ == '__main__':
    main()
