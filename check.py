#!/usr/bin/env python3

import json
import os
import re
import subprocess
import sys
import urllib.request


class Logger:
    def info(s):
        print('[INFO] {}'.format(s))

        # Flush to make it apeear immediately in automation log.
        sys.stdout.flush()


class FileUtils:
    def read(path):
        with open(path, 'r') as in_file:
            return in_file.read()

    @classmethod
    def read_json(cls, path):
        return json.loads(cls.read(path))

    def write(path, text):
        with open(path, 'w') as f:
            f.write(text)

    def write_binary(path, binary):
        with open(path, 'wb') as f:
            f.write(binary)

    def mkdir_p(path):
        if not os.path.exists(path):
            os.makedirs(path)


class Paths:
    TOKEN_PATH = os.path.join('.', 'token.json')
    STATUS_PATH = os.path.join('.', 'status.json')


class Config:
    __config_path = os.path.join('.', 'config.json')

    __config = FileUtils.read_json(__config_path)

    HG_API_URL = __config['hg_api_url']
    GITHUB_API_URL = __config['github_api_url']
    BMO_URL = __config['bmo_url']

    FILES = __config['files']


class RemoteRepository:
    def call(name, path):
        url = '{}{}{}'.format(Config.HG_API_URL, name, path)

        req = urllib.request.Request(url, None, {})
        response = urllib.request.urlopen(req)
        return response.read()

    @classmethod
    def call_json(cls, name, path):
        return json.loads(cls.call(name, path))

    @classmethod
    def file(cls, rev, path):
        return cls.call('raw-file', '/{}{}'.format(rev, path))

    @classmethod
    def log(cls, rev, path):
        return cls.call_json('json-log', '/{}{}'.format(rev, path))['entries']

    @classmethod
    def get_file_url(cls, rev, path):
        return '{}file/{}{}'.format(Config.HG_API_URL, rev, path)

    @classmethod
    def get_rev_url(cls, rev):
        return '{}rev/{}'.format(Config.HG_API_URL, rev)

    @classmethod
    def diff(cls, rev1, rev2, path):
        basename = os.path.basename(path)

        FileUtils.mkdir_p('tmp')

        name1 = '{}-{}'.format(rev1, basename)
        path1 = os.path.join('tmp', name1)
        content1 = cls.call('raw-file', '/{}{}'.format(rev1, path))
        FileUtils.write_binary(path1, content1)

        name2 = '{}-{}'.format(rev2, basename)
        path2 = os.path.join('tmp', name2)
        content2 = cls.call('raw-file', '/{}{}'.format(rev2, path))
        FileUtils.write_binary(path2, content2)

        p = subprocess.Popen(['diff', '-U', '8',
                              '--label', '{}{}'.format(rev1, path),
                              '--label', '{}{}'.format(rev2, path),
                              '-p',
                              name1, name2],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             cwd='tmp')
        output = p.stdout.read().decode()
        p.wait()

        return output


class UpdateChecker:
    @classmethod
    def check(cls):
        result = {}

        if os.path.exists(Paths.STATUS_PATH):
            status = FileUtils.read_json(Paths.STATUS_PATH)
        else:
            status = {}

        for path in Config.FILES:
            Logger.info('Checking {}'.format(path))

            logs = RemoteRepository.log('tip', path)
            node = logs[0]['node']

            if path in status:
                prevnode = status[path]

                changesets = []
                for changeset in logs:
                    if changeset['node'] == prevnode:
                        break
                    changesets.append(changeset)

                if prevnode != node:
                    diff = RemoteRepository.diff(prevnode, node, path)

                    result[path] = {
                        'prev': prevnode,
                        'now': node,
                        'changesets': changesets,
                        'diff': diff
                    }

            status[path] = node

        FileUtils.write(Paths.STATUS_PATH, json.dumps(status))

        return result


class GitHubAPI:
    __API_TOKEN = os.environ.get('POST_TOKEN')
    if not __API_TOKEN and os.path.exists(Paths.TOKEN_PATH):
        __API_TOKEN = FileUtils.read_json(Paths.TOKEN_PATH)['post_token']

    @classmethod
    def post(cls, path, query, data):
        query_string = '&'.join(
            map(lambda x: '{}={}'.format(x[0], x[1]),
                query))

        url = '{}{}?{}'.format(Config.GITHUB_API_URL, path, query_string)
        if cls.__API_TOKEN:
            headers = {
                'Authorization': 'token {}'.format(cls.__API_TOKEN),
            }
        else:
            headers = {}

        headers['Content-Type'] = 'application/json'

        req = urllib.request.Request(url, json.dumps(data).encode(), headers)
        response = urllib.request.urlopen(req)
        return json.loads(response.read())


class IssueOpener:
    BUG_PAT = re.compile(r'(bug(?:\s*:\s*|=|\s+)(\d+))', re.I)

    @classmethod
    def linkify(cls, text):
        return cls.BUG_PAT.sub(r'[\1]({}show_bug.cgi?id=\2)'.format(
            Config.BMO_URL), text)

    @classmethod
    def open(cls, result):
        handled_nodes = set()
        total_changesets = []

        paths = []

        contents = []

        contents.append('# Files')
        contents.append('')

        for (path, data) in sorted(result.items()):
            paths.append(path)

            now = data['now']
            url = RemoteRepository.get_file_url(now, path)
            contents.append('* [`{}`]({})'.format(path, url))

            for changeset in data['changesets']:
                node = changeset['node']
                if node in handled_nodes:
                    continue
                handled_nodes.add(node)
                total_changesets.append(changeset)

        if len(paths) == 0:
            Logger.info('No changes')
            return

        contents.append('')
        contents.append('# Changesets')
        contents.append('')

        latest_node = None

        for changeset in sorted(total_changesets,
                                key=lambda c: c['pushdate'][0]):
            if latest_node is None:
                latest_node = changeset['node']

            url = RemoteRepository.get_rev_url(changeset['node'])
            contents.append('* {}'.format(url))
            subject = cls.linkify(changeset['desc'].split('\n')[0])
            contents.append('  {}  '.format(subject))

        contents.append('')
        contents.append('# Diffs')
        contents.append('')

        for (path, data) in sorted(result.items()):
            now = data['now']
            url = RemoteRepository.get_file_url(now, path)
            diff = data['diff']

            contents.append('## [`{}`]({})'.format(path, url))

            contents.append("""
```
{}```
""".format(diff))

        body = '\n'.join(contents)

        if len(body) > 60000:
            body = body[0:60000] + "\n(comment length limit exceeeded)"

        opcodes_path = '/js/src/vm/Opcodes.h'
        if opcodes_path in paths:
            main_path = opcodes_path
        else:
            main_path = paths[0]

        if len(paths) > 2:
            title = '{} and {} more files have been updated'.format(
                main_path, len(paths) - 1)
        elif len(paths) == 2:
            title = '{} and one more file have been updated'.format(
                main_path)
        else:
            title = '{} has been updated'.format(
                main_path)

        title = '{} ({})'.format(title, latest_node[0:8])

        Logger.info('Opening Issue')
        Logger.info('title: {}'.format(title))
        Logger.info('body: {}'.format(body))

        GitHubAPI.post('issues', [], {
            'title': title,
            'body': body,
        })

        raise "error"


result = UpdateChecker.check()
IssueOpener.open(result)
