"""Read-only remote probe, sent to the existing K1 SSH MCP as Python source.

Only selected fields leave the host. Docker environment, raw logs and connection
strings must never be returned. No file/container/process mutations are performed.
"""
import json
import re
import socket
import subprocess
from datetime import datetime, timezone


def run(argv):
    process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             encoding='utf-8', errors='replace', timeout=20)
    if process.returncode:
        raise RuntimeError('COMMAND_FAILED')
    return process.stdout


def heartbeat_from_logs(logs, sid, app):
    kind = 'game' if app == 'gs' else 'battle'
    marker = 'HSet(gm:cluster:' + kind + ':list,' + str(sid) + ','
    found = []
    for line in logs.splitlines():
        # Support plain logs and JSON envelopes containing the same log message.
        candidates = [line]
        try:
            envelope = json.loads(line)
            if isinstance(envelope, dict):
                candidates.extend(v for v in envelope.values() if isinstance(v, str))
        except ValueError:
            pass
        for text in candidates:
            at = text.find(marker)
            if at < 0:
                continue
            start = text.find('{', at + len(marker))
            if start < 0:
                continue
            try:
                value, _ = json.JSONDecoder().raw_decode(text[start:])
                if value.get('type') == kind and str(value.get('sid')) == str(sid):
                    found.append(value)
            except (ValueError, AttributeError):
                pass
    if not found:
        return {}
    latest = max(found, key=lambda h: h.get('runningTime', 0))
    allowed = {'type', 'sid', 'ip', 'mainPort', 'running', 'runningPid', 'runningTime',
               'startTime', 'updateTime', 'pprofPort', 'codeVer', 'timeOffsetSec'}
    return {k: v for k, v in latest.items() if k in allowed}


def main(options):
    stage = 'scope'
    try:
        sid, owner, app = options['sid'], options['owner'], options['app']
        if not re.fullmatch(r'[1-9][0-9]*', sid) or not re.fullmatch(r'[A-Za-z0-9_.-]+', owner) or app not in {'gs', 'battle'}:
            raise RuntimeError('INVALID_PROBE_SCOPE')
        stage = 'host'
        addresses = run(['hostname', '-I']).split()
        if options['host'] not in addresses:
            raise RuntimeError('SSH_HOST_MISMATCH')
        stage = 'inventory'
        names = run(['docker', 'ps', '-a', '--format', '{{.Names}}']).splitlines()
        # Include stopped and unfamiliar names ending in the target SID. The
        # shared dev<SID> database makes another owner's stopped server relevant.
        occupants = [name for name in names if re.search(r'(?:^|[-_])' + re.escape(sid) + r'$', name)]
        result = {'host': options['host'], 'checkedAt': datetime.now(timezone.utc).isoformat(),
                  'inventoryComplete': True, 'containers': occupants}
        if options.get('runtime'):
            stage = 'inspect'
            name = 'remote-' + owner + '-' + app + '-' + sid
            inspected = json.loads(run(['docker', 'inspect', name]))[0]
            state, config = inspected['State'], inspected['Config']
            cmd = config.get('Cmd') or []
            stage = 'process'
            top = run(['docker', 'top', name, '-eo', 'pid,args'])
            mode = 'game'
            process_running = any(re.search(r'(?:^|\s)' + mode + r'(?:\s|$)', line)
                                  and '--sid=' + sid in line.split() for line in top.splitlines()[1:])
            result['container'] = {'name': inspected['Name'].lstrip('/'), 'running': state['Running'],
                'startedAt': state['StartedAt'], 'image': config['Image'], 'imageId': inspected['Image'],
                'command': [x for x in cmd if x in {'game', '--battle'} or x.startswith(('--nacos=', '--csv=', '--sid='))],
                'processRunning': process_running, 'networkMode': inspected['HostConfig']['NetworkMode']}
            # Docker routes app logs to either stdout or stderr; capture both,
            # parse on host, and never emit raw lines.
            stage = 'heartbeat'
            logged = subprocess.run(['docker', 'logs', '--since', '90s', '--tail', '5000', name],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding='utf-8', errors='replace', timeout=20)
            if logged.returncode:
                raise RuntimeError('LOG_READ_FAILED')
            hb = heartbeat_from_logs(logged.stdout + '\n' + logged.stderr, sid, app)
            result['heartbeat'] = hb
            result['tcp'] = []
            port = hb.get('mainPort')
            if hb.get('ip') == options['host'] and type(port) is int and 0 < port < 65536:
                reachable = False
                try:
                    with socket.create_connection((hb['ip'], port), timeout=3):
                        reachable = True
                except OSError:
                    pass
                result['tcp'].append({'host': hb['ip'], 'port': port, 'reachable': reachable})
            # Timestamp after collection so freshness covers the complete probe.
            result['checkedAt'] = datetime.now(timezone.utc).isoformat()
        print('K1_ENV_PROBE=' + json.dumps(result, ensure_ascii=False))
    except Exception as error:
        print('K1_ENV_PROBE=' + json.dumps({'error': 'READ_ONLY_PROBE_FAILED', 'stage': stage, 'errorType': type(error).__name__}))
