import time

from fastapi.testclient import TestClient

from workbench.app import create_app


def test_local_session_upload_run_preview_and_package(tmp_path, monkeypatch):
    monkeypatch.setenv('DATA_WORKBENCH_RUNTIME', str(tmp_path / 'runtime'))
    app = create_app(tmp_path / 'projects')
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.post('/api/projects', json={'name':'Blocked'}).status_code == 403
        token = client.get('/api/session').json()['token']
        headers = {'x-workbench-token':token}
        assert client.post('/api/projects',json={'name':'Blocked'},headers={**headers,'origin':'https://elsewhere.example'}).status_code == 403
        p = client.post('/api/projects', json={'name':'API demo'}, headers=headers).json()
        prefix = '/api/projects/' + p['id']
        bad = client.post(prefix+'/sources',files={'file':('bad.xlsx',b'not a workbook','application/octet-stream')},headers=headers)
        assert bad.status_code==400 and 'Cannot inspect' in bad.json()['error']
        upload = client.post(prefix+'/sources',files={'file':('rows.csv',b'id,amount\n001,12.3456\n','text/csv')},headers=headers)
        assert upload.status_code == 201, upload.text
        assert client.post(prefix+'/suggest-recipe',headers=headers).status_code == 200
        run = client.post(prefix+'/runs',headers=headers).json()
        for _ in range(200):
            run = client.get(prefix+'/runs/'+run['id']).json()
            if run['state'] not in ('running','queued'):
                break
            time.sleep(.03)
        assert run['state']=='completed',run
        result = client.get(prefix+'/runs/'+run['id']+'/rows?table=rows').json()
        assert result['total']==1
        amount = result['rows'][0][result['columns'].index('amount')]
        assert amount=='12.345600000000'
        package = client.get(prefix+'/runs/'+run['id']+'/package')
        assert package.status_code==200 and package.content[:2]==b'PK'
        assert client.get(prefix+'/runs/'+run['id']+'/files/not-found.csv').status_code==400
    app.state.store.worker.shutdown()
