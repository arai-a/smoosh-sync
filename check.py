#!/usr/bin/env python3

import json
import os
import subprocess
import urllib.request


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

        p = subprocess.Popen(['diff', '-U', '8', name1, name2],
                             stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT,
                             cwd='tmp')
        output = p.stdout.read().decode()
        p.wait()

        output = output.replace(name1, '{}{}'.format(rev1, path))
        output = output.replace(name2, '{}{}'.format(rev2, path))
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
            logs = RemoteRepository.log('tip', path)
            node = logs[0]['node']

            if path in status:
                prevnode = status[path]
                if prevnode != node:
                    diff = RemoteRepository.diff(prevnode, node, path)

                    result[path] = {
                        'prev': prevnode,
                        'now': node,
                        'diff': diff
                    }

            status[path] = node

        #FileUtils.write(Paths.STATUS_PATH, json.dumps(status))

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
    def open(result):
        for (path, data) in result.items():
            prev = data['prev']
            short1 = prev[0:8]
            now = data['now']
            short2 = now[0:8]
            diff = data['diff']
            url = RemoteRepository.get_file_url(now, path)

            GitHubAPI.post('issues', [], {
                'title': '{} has been updated {} => {}'.format(
                    path, short1, short2),
                'body': """
{}
```
{}
```
""".format(url, diff),
            })


result = UpdateChecker.check()
IssueOpener.open(result)
