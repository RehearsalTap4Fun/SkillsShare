"""K1 dev test deployment: inspect, prepare, submit once, poll exact queue/build.

Credentials stay in the existing local config. Authorization references in a
plan record human consent; this program cannot authenticate that consent.
"""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


ALLOWED_PARAMETERS = {'corebranch', 'branchName', 'app', 'server_id', 'ALIVE', 'DownKey', 'kvk_keep_servers'}


class DeploymentError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise DeploymentError(code)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


WORKFLOW_FIELDS = {'groupId', 'sender', 'senderId', 'runtimeHost', 'workflowBaseUrl'}


def configured(value):
    return (nonempty(value) and value == value.strip() and value.casefold() not in {'unknown', 'todo', 'tbd', 'none', 'null'} and not re.search(r'[<>{}\r\n]', value)
            and not re.search(r'(?i)(placeholder|change[-_ ]?me|your[-_ ]|example\.(?:com|invalid|org))', value))


def validate_config(config, workflow=False):
    require(isinstance(config, dict) and set(config) <= {
        'baseUrl', 'username', 'apiToken', 'jobs', 'environmentWorkflow', 'deploymentDefaults'}, 'JENKINS_CONFIG_FIELDS_INVALID')
    require(all(configured(config.get(k)) for k in ['baseUrl', 'username', 'apiToken']), 'JENKINS_CONFIG_REQUIRED')
    require(re.fullmatch(r'[A-Za-z0-9_.-]+', config['username']), 'JENKINS_CONFIG_USERNAME_INVALID')
    parts = urllib.parse.urlsplit(config['baseUrl'])
    require(parts.scheme in {'http', 'https'} and parts.netloc and not parts.username
            and not parts.password and not parts.query and not parts.fragment, 'JENKINS_CONFIG_INVALID')
    jobs = config.get('jobs')
    require(isinstance(jobs, dict) and set(jobs) == {'deploy', 'backup', 'child'}
            and all(configured(v) and not re.search(r'[/\\?#%]', v) and v not in {'.', '..'} for v in jobs.values())
            and len(set(jobs.values())) == 3, 'JENKINS_CONFIG_JOBS_INVALID')
    settings = config.get('environmentWorkflow')
    if workflow or settings is not None:
        require(isinstance(settings, dict) and set(settings) == WORKFLOW_FIELDS
                and all(configured(v) for v in settings.values()), 'JENKINS_CONFIG_WORKFLOW_REQUIRED')
        url = urllib.parse.urlsplit(settings['workflowBaseUrl'])
        require(url.scheme in {'http', 'https'} and url.netloc and not url.username
                and not url.password and not url.query and not url.fragment, 'JENKINS_CONFIG_WORKFLOW_URL_INVALID')
        require(re.fullmatch(r'[A-Za-z0-9.:-]+', settings['runtimeHost']), 'JENKINS_CONFIG_RUNTIME_HOST_INVALID')
    defaults = config.get('deploymentDefaults', {})
    require(isinstance(defaults, dict) and set(defaults) <= {'ALIVE'}
            and ('ALIVE' not in defaults or str(defaults['ALIVE']) in {'1','2','3','4','5'}), 'JENKINS_CONFIG_DEFAULTS_INVALID')
    return config


def load_config(path=None, workflow=False):
    path = path or os.environ.get('K1_JENKINS_CONFIG')
    require(configured(path), 'JENKINS_CONFIG_PATH_REQUIRED')
    return validate_config(read_json(path), workflow=workflow)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def check_destination(path):
    from user_config import check_write_path, UserConfigError
    try:
        return check_write_path(path)
    except UserConfigError as error:
        raise DeploymentError(str(error)) from None


def write_new(path, value):
    path = check_destination(path)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def save_receipt(path, value):
    path = check_destination(path)
    temporary = check_destination(path.with_name(path.name + '.tmp'))
    with temporary.open('w', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
    temporary.replace(path)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Jenkins:
    def __init__(self, config):
        validate_config(config)
        self.config = deepcopy(config)
        self.job = config['jobs']['deploy']
        self.job_path = '/job/' + urllib.parse.quote(self.job, safe='') + '/'
        self.base = config['baseUrl'].rstrip('/')
        parts = urllib.parse.urlsplit(self.base)
        require(parts.scheme in {'http', 'https'} and parts.netloc and not parts.username
                and not parts.password and not parts.query and not parts.fragment, 'JENKINS_CONFIG_INVALID')
        credential = config['username'] + ':' + config['apiToken']
        self.authorization = 'Basic ' + base64.b64encode(credential.encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect(), urllib.request.HTTPCookieProcessor(CookieJar()))

    def safe_url(self, value):
        url = urllib.parse.urljoin(self.base + '/', value)
        parts = urllib.parse.urlsplit(url)
        base = urllib.parse.urlsplit(self.base)
        require(parts.scheme == base.scheme and parts.netloc == base.netloc
                and parts.path.startswith(base.path + '/') and not parts.fragment
                and not any(urllib.parse.unquote(p) in {'.', '..'} for p in parts.path.split('/')),
                'UNSAFE_JENKINS_URL')
        return url

    def request(self, path, data=None, headers=None):
        url = self.safe_url(path)
        req = urllib.request.Request(url, data=data, headers={
            'Authorization': self.authorization, **(headers or {})})
        try:
            with self.opener.open(req, timeout=30) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as e:
            raise DeploymentError('JENKINS_HTTP_' + str(e.code)) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DeploymentError('JENKINS_NETWORK_ERROR') from None

    def get(self, path, tree=None):
        if tree:
            path += '?' + urllib.parse.urlencode({'tree': tree})
        _, _, raw = self.request(path)
        try:
            return json.loads(raw)
        except ValueError:
            raise DeploymentError('JENKINS_INVALID_JSON') from None

    def inspect(self):
        data = self.get(self.base + self.job_path + 'api/json',
                        'name,buildable,property[parameterDefinitions[name,type,description,choices,defaultParameterValue[value]]]')
        definitions = {}
        for prop in data.get('property', []):
            for p in prop.get('parameterDefinitions', []):
                if p.get('name') in ALLOWED_PARAMETERS:
                    p = deepcopy(p)
                    if p['name'] == 'DownKey':
                        p.pop('defaultParameterValue', None)
                    definitions[p['name']] = p
        return {'job': self.job, 'baseUrl': self.base, 'buildable': data.get('buildable'), 'parameters': definitions}


def validate_request(request, metadata):
    scope, params = request.get('scope', {}), request.get('parameters', {})
    require(scope.get('environment') == 'dev' and nonempty(scope.get('authorization')), 'DEPLOYMENT_SCOPE_REQUIRED')
    require(metadata.get('buildable') is True, 'JOB_NOT_BUILDABLE')
    require(isinstance(scope.get('serverIds'), list) and all(isinstance(s, str) for s in scope['serverIds']), 'SID_SCOPE_INVALID')
    require(set(params) <= ALLOWED_PARAMETERS and {'corebranch', 'branchName', 'app', 'server_id', 'ALIVE'} <= set(params), 'PARAMETERS_INVALID')
    require(all(isinstance(v, str) for v in params.values()), 'PARAMETERS_INVALID')
    sid = params['server_id']
    require(sid in scope['serverIds'], 'SID_OUT_OF_SCOPE')
    require(re.fullmatch(r'[1-9][0-9]*', sid) is not None, 'SID_INVALID')
    require(params['app'] in {'gs', 'battle'}, 'APP_OUT_OF_SCOPE')
    require((params['app'] == 'gs') == (int(sid) < 10000), 'SID_APP_MISMATCH')
    require(not params.get('DownKey'), 'IMPORT_KEY_MUST_USE_ENV')
    if request.get('dataImportKeyEnv'):
        require(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', request['dataImportKeyEnv']) is not None
                and nonempty(scope.get('dataImportAuthorization')), 'IMPORT_AUTHORIZATION_REQUIRED')
    definitions = metadata['parameters']
    for name, value in params.items():
        require(name in definitions, 'PARAMETER_NOT_IN_LIVE_JOB')
        if 'choices' in definitions[name]:
            require(value in definitions[name]['choices'], 'PARAMETER_CHOICE_INVALID')
    sources = request.get('sources', {})
    for role, param in [('server', 'corebranch'), ('config', 'branchName')]:
        source = sources.get(role, {})
        require(source.get('ref') == params[param], 'SOURCE_REF_MISMATCH')
        require(re.fullmatch(r'[0-9a-fA-F]{40}', source.get('sha', '')) is not None and nonempty(source.get('evidence')), 'FROZEN_SOURCE_REQUIRED')
        require(re.fullmatch(r'origin/[A-Za-z0-9_./-]+', params[param]) is not None
                and '..' not in params[param], 'BRANCH_INVALID')


def prepare(client, request):
    metadata = client.inspect()
    validate_request(request, metadata)
    plan = deepcopy(request)
    plan.update(schemaVersion=1, job=client.job, baseUrl=client.base, preparedAt=timestamp())
    plan['planSha256'] = digest(plan)
    return plan


def submit(client, plan, receipt_path):
    receipt_path = check_destination(receipt_path)
    require(not receipt_path.exists(), 'RECEIPT_EXISTS_RECONCILE_DO_NOT_RESUBMIT')
    payload = {k: v for k, v in plan.items() if k != 'planSha256'}
    require(plan.get('planSha256') == digest(payload), 'PLAN_CHANGED')
    require(plan.get('baseUrl') == client.base and plan.get('job') == client.job, 'DEPLOYMENT_TARGET_CHANGED')
    validate_request(plan, client.inspect())
    params = deepcopy(plan['parameters'])
    if plan.get('dataImportKeyEnv'):
        secret = os.environ.get(plan['dataImportKeyEnv'])
        require(nonempty(secret), 'IMPORT_KEY_ENV_MISSING')
        params['DownKey'] = secret
    headers = {'Content-Type': 'application/x-www-form-urlencoded'}
    try:
        crumb = client.get(client.base + '/crumbIssuer/api/json')
        field = crumb.get('crumbRequestField', '')
        require(re.fullmatch(r'[A-Za-z0-9-]+', field) is not None and field.lower() not in {'authorization', 'host', 'content-type'}, 'CRUMB_INVALID')
        headers[field] = crumb['crumb']
    except DeploymentError as e:
        if str(e) != 'JENKINS_HTTP_404':
            raise
    receipt = {'schemaVersion': 1, 'baseUrl': client.base, 'job': client.job, 'planSha256': plan['planSha256'],
               'status': 'submission_pending', 'submittedAt': timestamp(),
               'parameters': {k: v for k, v in params.items() if k != 'DownKey'},
               'dataImport': bool(params.get('DownKey')), 'sources': plan['sources']}
    try:
        write_new(receipt_path, receipt)
    except FileExistsError:
        raise DeploymentError('RECEIPT_EXISTS_RECONCILE_DO_NOT_RESUBMIT') from None
    try:
        status, response_headers, _ = client.request(client.base + client.job_path + 'buildWithParameters',
                                                     urllib.parse.urlencode(params).encode(), headers)
        require(status == 201, 'SUBMISSION_OUTCOME_UNKNOWN')
        location = response_headers.get('Location')
        require(bool(location), 'QUEUE_LOCATION_MISSING')
        queue_url = client.safe_url(location)
        require(re.fullmatch(re.escape(client.base) + r'/queue/item/[0-9]+/', queue_url) is not None, 'QUEUE_LOCATION_INVALID')
        receipt.update(status='queued', queueUrl=queue_url)
    except Exception as e:
        receipt.update(status='submission_unknown', error=str(e) if isinstance(e, DeploymentError) else type(e).__name__)
        save_receipt(receipt_path, receipt)
        raise DeploymentError(receipt['error']) from None
    save_receipt(receipt_path, receipt)
    return receipt


def poll(client, receipt_path):
    path = check_destination(receipt_path)
    receipt = read_json(path)
    require(receipt.get('baseUrl') == client.base and receipt.get('job') == client.job, 'DEPLOYMENT_TARGET_CHANGED')
    if not receipt.get('buildUrl'):
        require(bool(receipt.get('queueUrl')), 'RECONCILE_SUBMISSION_BEFORE_RETRY')
        queue = client.safe_url(receipt['queueUrl'])
        require(re.fullmatch(re.escape(client.base) + r'/queue/item/[0-9]+/', queue) is not None, 'QUEUE_LOCATION_INVALID')
        item = client.get(queue + 'api/json')
        if item.get('cancelled'):
            receipt['status'] = 'cancelled'
        elif item.get('executable'):
            executable = item['executable']
            number = executable.get('number')
            require(type(number) is int and number > 0, 'BUILD_NUMBER_INVALID')
            build_url = client.safe_url(executable['url'])
            require(build_url == client.base + client.job_path + str(number) + '/', 'BUILD_JOB_MISMATCH')
            receipt.update(buildNumber=number, buildUrl=build_url, status='building')
        save_receipt(path, receipt)
    if receipt.get('buildUrl'):
        require(type(receipt.get('buildNumber')) is int, 'BUILD_NUMBER_INVALID')
        expected = client.base + client.job_path + str(receipt['buildNumber']) + '/'
        require(receipt['buildUrl'] == expected, 'BUILD_JOB_MISMATCH')
        build = client.get(expected + 'api/json', 'number,building,result')
        require(build.get('number') == receipt['buildNumber'], 'BUILD_NUMBER_MISMATCH')
        receipt['result'] = build.get('result')
        receipt['status'] = ('building' if build.get('building') else
                             'build_succeeded_runtime_unverified' if build.get('result') == 'SUCCESS' else 'build_failed')
        save_receipt(path, receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['inspect', 'prepare', 'submit', 'poll'])
    parser.add_argument('--config', help='Private JSON path; otherwise K1_JENKINS_CONFIG is required')
    parser.add_argument('--request')
    parser.add_argument('--plan')
    parser.add_argument('--receipt')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        # Reject every write destination before constructing or contacting the client.
        if args.command == 'prepare':
            require(bool(args.request and args.output), 'REQUEST_AND_OUTPUT_REQUIRED')
            args.output = check_destination(args.output)
        elif args.command == 'submit':
            require(bool(args.plan and args.receipt), 'PLAN_AND_RECEIPT_REQUIRED')
            args.receipt = check_destination(args.receipt)
        elif args.command == 'poll':
            require(bool(args.receipt), 'RECEIPT_REQUIRED')
            args.receipt = check_destination(args.receipt)
        client = Jenkins(load_config(args.config))
        if args.command == 'inspect':
            result = client.inspect()
        elif args.command == 'prepare':
            require(bool(args.request and args.output), 'REQUEST_AND_OUTPUT_REQUIRED')
            result = prepare(client, read_json(args.request))
            write_new(args.output, result)
        elif args.command == 'submit':
            require(bool(args.plan and args.receipt), 'PLAN_AND_RECEIPT_REQUIRED')
            result = submit(client, read_json(args.plan), args.receipt)
        else:
            require(bool(args.receipt), 'RECEIPT_REQUIRED')
            result = poll(client, args.receipt)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (DeploymentError, OSError, ValueError, KeyError, TypeError) as e:
        print(json.dumps({'error': str(e) if isinstance(e, DeploymentError) else type(e).__name__}))
        return 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
