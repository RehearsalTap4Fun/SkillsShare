import importlib.util
import json
import sys
from pathlib import Path
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/jenkins_test_environment.py'
JOB = 'deploy-test'


@pytest.fixture(autouse=True)
def jenkins_workspace(isolated_user_profile, tmp_path, monkeypatch):
    profile = json.loads(isolated_user_profile.read_text(encoding='utf-8'))
    profile['workspaceRoot'] = str(tmp_path)
    isolated_user_profile.write_text(json.dumps(profile), encoding='utf-8')
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts'))


@pytest.fixture
def jenkins():
    assert SCRIPT.exists(), 'Jenkins test deployment adapter is missing'
    spec = importlib.util.spec_from_file_location('jenkins_test_environment', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def request_data():
    return {'scope': {'environment': 'dev', 'serverIds': ['7001'], 'authorization': 'user message: deploy test server'},
            'sources': {'server': {'ref': 'origin/dev', 'sha': 'a' * 40, 'evidence': 'frozen server manifest'},
                        'config': {'ref': 'origin/dev_coldtest', 'sha': 'b' * 40, 'evidence': 'verified config branch'}},
            'parameters': {'corebranch': 'origin/dev', 'branchName': 'origin/dev_coldtest',
                           'server_id': '7001', 'app': 'gs', 'ALIVE': '5', 'DownKey': ''}}


@pytest.fixture
def api():
    calls = []
    control = {'unknown': False, 'foreign': False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass

        def reply(self, data, code=200, **headers):
            self.send_response(code)
            for key, value in headers.items(): self.send_header(key, value)
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self):
            calls.append(('GET', self.path, None))
            if '/crumbIssuer/' in self.path:
                self.reply({'crumbRequestField': 'Jenkins-Crumb', 'crumb': 'test-crumb'})
            elif '/queue/item/7/' in self.path:
                url = base + '/job/' + self.server.job_path + '/42/'
                self.reply({'id': 7, 'cancelled': False, 'executable': {'number': 42, 'url': url}})
            elif '/42/api/json' in self.path:
                self.reply({'number': 42, 'building': False, 'result': 'SUCCESS'})
            elif '/job/' in self.path and '/api/json' in self.path:
                defs = []
                for name in ['corebranch', 'branchName', 'server_id', 'DownKey', 'kvk_keep_servers']:
                    defs.append({'name': name, 'type': 'StringParameterDefinition', 'defaultParameterValue': {'value': ''}})
                defs += [{'name': 'app', 'type': 'ChoiceParameterDefinition', 'choices': ['gs', 'battle']},
                         {'name': 'ALIVE', 'type': 'ChoiceParameterDefinition', 'choices': ['1','2','3','4','5']}]
                self.reply({'name': JOB, 'buildable': True, 'property': [{'parameterDefinitions': defs}],
                            'lastBuild': {'number': 999, 'result': 'SUCCESS'}})
            else: self.reply({}, 404)

        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length'])).decode()
            calls.append(('POST', self.path, parse_qs(body, keep_blank_values=True)))
            if control['unknown']:
                self.reply({'private': 'DO_NOT_PRINT'}, 503)
            else:
                self.reply({}, 201, Location=('http://example.invalid/queue/item/7/' if control['foreign'] else base + '/queue/item/7/'))

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    base = 'http://127.0.0.1:' + str(server.server_port) + '/jenkins'
    from urllib.parse import quote
    server.job_path = quote(JOB, safe='')
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield {'baseUrl': base, 'username': 'tester', 'apiToken': 'PRIVATE_TOKEN', 'jobs': {'deploy': JOB, 'backup': 'backup-test', 'child': 'child-test'}}, calls, control
    server.shutdown()
    server.server_close()
    worker.join()


def test_submitted_queue_is_tracked_instead_of_unrelated_latest_build(jenkins, api, request_data, tmp_path):
    config, calls, _ = api
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    receipt_path = tmp_path / 'receipt.json'
    receipt = jenkins.submit(client, plan, receipt_path)
    assert receipt['status'] == 'queued'
    result = jenkins.poll(client, receipt_path)
    assert result['buildNumber'] == 42
    assert result['status'] == 'build_succeeded_runtime_unverified'
    assert not any('lastBuild' in path for _, path, _ in calls)
    posted = [body for method, _, body in calls if method == 'POST']
    assert posted[0]['corebranch'] == ['origin/dev']
    assert posted[0]['branchName'] == ['origin/dev_coldtest']
    assert 'PRIVATE_TOKEN' not in receipt_path.read_text(encoding='utf-8')


@pytest.mark.parametrize('field,value,error', [
    ('server_id','7002','SID_OUT_OF_SCOPE'),
    ('app','battle','SID_APP_MISMATCH'),
    ('ALIVE','9','PARAMETER_CHOICE_INVALID'),
    ('corebranch','origin/not-verified','SOURCE_REF_MISMATCH'),
    ('DownKey','raw-private-key','IMPORT_KEY_MUST_USE_ENV'),
])
def test_bad_deployment_parameters_never_submit(jenkins, api, request_data, field, value, error):
    config, calls, _ = api
    request_data['parameters'][field] = value
    with pytest.raises(jenkins.DeploymentError, match=error): jenkins.prepare(jenkins.Jenkins(config), request_data)
    assert not any(c[0] == 'POST' for c in calls)


def test_unknown_submit_outcome_persists_receipt_and_is_never_reposted(jenkins, api, request_data, tmp_path):
    config, calls, control = api
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    control['unknown'] = True
    path = tmp_path / 'receipt.json'
    with pytest.raises(jenkins.DeploymentError): jenkins.submit(client, plan, path)
    receipt = json.loads(path.read_text(encoding='utf-8'))
    assert receipt['status'] == 'submission_unknown'
    with pytest.raises(jenkins.DeploymentError, match='RECEIPT_EXISTS'): jenkins.submit(client, plan, path)
    assert len([x for x in calls if x[0] == 'POST']) == 1
    assert 'DO_NOT_PRINT' not in path.read_text(encoding='utf-8')


def test_queue_location_must_stay_on_configured_jenkins(jenkins, api, request_data, tmp_path):
    config, calls, control = api
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    control['foreign'] = True
    with pytest.raises(jenkins.DeploymentError, match='UNSAFE_JENKINS_URL'):
        jenkins.submit(client, plan, tmp_path / 'receipt.json')
    assert len([x for x in calls if x[0] == 'POST']) == 1


def test_import_secret_is_read_only_at_submit_and_never_in_receipt(jenkins, api, request_data, tmp_path, monkeypatch):
    config, calls, _ = api
    request_data['dataImportKeyEnv'] = 'K1_TEST_IMPORT_KEY'
    request_data['scope']['dataImportAuthorization'] = 'user approved snapshot import'
    monkeypatch.setenv('K1_TEST_IMPORT_KEY', 'SENSITIVE_DOWNLOAD_KEY')
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    path = tmp_path / 'receipt.json'
    jenkins.submit(client, plan, path)
    assert [c[2] for c in calls if c[0] == 'POST'][0]['DownKey'] == ['SENSITIVE_DOWNLOAD_KEY']
    assert 'SENSITIVE_DOWNLOAD_KEY' not in path.read_text(encoding='utf-8')
    assert 'SENSITIVE_DOWNLOAD_KEY' not in json.dumps(plan)


def test_mutated_scope_or_plan_cannot_be_submitted(jenkins, api, request_data, tmp_path):
    config, calls, _ = api
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    plan['parameters']['server_id'] = '7002'
    with pytest.raises(jenkins.DeploymentError, match='PLAN_CHANGED'):
        jenkins.submit(client, plan, tmp_path / 'receipt.json')
    assert not any(c[0] == 'POST' for c in calls)


def test_no_config_path_fallback(jenkins, monkeypatch):
    monkeypatch.delenv('K1_JENKINS_CONFIG', raising=False)
    with pytest.raises(jenkins.DeploymentError, match='CONFIG_PATH_REQUIRED'):
        jenkins.load_config()


@pytest.mark.parametrize('jobs', [None, {}, {'deploy': '<DEPLOY_JOB>'},
    {'deploy': 'deploy-test', 'backup': 'backup-test', 'child': 'child-test', 'typo': 'bad'}])
def test_invalid_jobs_fail_before_network(jenkins, jobs):
    config = {'baseUrl': 'http://jenkins.test', 'username': 'tester', 'apiToken': 'test-only-token'}
    if jobs is not None:
        config['jobs'] = jobs
    with pytest.raises(jenkins.DeploymentError, match='CONFIG'):
        jenkins.Jenkins(config)


def test_changed_job_rejects_saved_plan_before_remote_read(jenkins, api, request_data, tmp_path, monkeypatch):
    config, calls, _ = api
    client = jenkins.Jenkins(config)
    plan = jenkins.prepare(client, request_data)
    config['jobs']['deploy'] = 'different-deploy'
    changed = jenkins.Jenkins(config)
    calls.clear()
    with pytest.raises(jenkins.DeploymentError, match='TARGET_CHANGED'):
        jenkins.submit(changed, plan, tmp_path / 'receipt.json')
    assert calls == []


def test_config_path_environment_and_explicit_override(jenkins, tmp_path, monkeypatch):
    config = {'baseUrl': 'http://jenkins.test', 'username': 'tester', 'apiToken': 'test-only-token',
              'jobs': {'deploy': 'deploy-test', 'backup': 'backup-test', 'child': 'child-test'}}
    good = tmp_path / 'private.json'
    good.write_text(json.dumps(config), encoding='utf-8')
    monkeypatch.setenv('K1_JENKINS_CONFIG', str(good))
    assert jenkins.load_config() == config
    monkeypatch.setenv('K1_JENKINS_CONFIG', str(tmp_path / 'missing.json'))
    assert jenkins.load_config(str(good)) == config


@pytest.mark.parametrize('key,value', [('baseUrl','https://example.invalid'), ('username','<USERNAME>'),
    ('apiToken','CHANGE_ME'), ('username','user name')])
def test_placeholder_credentials_rejected_before_network(jenkins, key, value):
    config = {'baseUrl': 'http://jenkins.test', 'username': 'tester', 'apiToken': 'test-only-token',
              'jobs': {'deploy': 'deploy-test', 'backup': 'backup-test', 'child': 'child-test'}}
    config[key] = value
    with pytest.raises(jenkins.DeploymentError, match='CONFIG'):
        jenkins.Jenkins(config)


@pytest.mark.parametrize('value', ['unknown', 'UNKNOWN', 'TODO', 'todo', 'TBD', 'null'])
def test_unresolved_values_are_not_configured(jenkins, value):
    assert not jenkins.configured(value)


@pytest.mark.parametrize('command', ['prepare', 'submit', 'poll'])
def test_cli_rejects_formal_repository_output_before_network(jenkins, isolated_user_profile, monkeypatch, command, capsys):
    profile = json.loads(isolated_user_profile.read_text(encoding='utf-8'))
    destination = Path(profile['repositories']['server']) / 'forbidden.json'
    argv = ['jenkins_test_environment.py', command]
    argv += ['--request', 'request.json', '--output', str(destination)] if command == 'prepare' else (
        ['--plan', 'plan.json', '--receipt', str(destination)] if command == 'submit' else ['--receipt', str(destination)])
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(jenkins, 'Jenkins', lambda *args: pytest.fail('Must not construct client'))
    assert jenkins.main() == 1
    assert json.loads(capsys.readouterr().out)['error'] == 'FORMAL_REPOSITORY_READ_ONLY'
    assert not destination.exists()


@pytest.mark.parametrize('writer', ['write_new', 'save_receipt'])
def test_receipt_helpers_cannot_write_formal_repository(jenkins, isolated_user_profile, writer):
    profile = json.loads(isolated_user_profile.read_text(encoding='utf-8'))
    destination = Path(profile['repositories']['server']) / 'forbidden.json'
    with pytest.raises(jenkins.DeploymentError, match='FORMAL_REPOSITORY_READ_ONLY'):
        getattr(jenkins, writer)(destination, {'status': 'test'})
    assert not destination.exists()
