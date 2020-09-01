import json
import time
import os
import argparse
import requests
from requests.auth import HTTPBasicAuth
import boto
from boto.s3.key import Key
import wizard
from time import gmtime, strftime


def read_config():
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_config.json')
    with open(config_path, 'r') as config_file:
        return json.load(config_file)


class Atlassian:
    def __init__(self, config):
        print(strftime("%Y-%m-%d %H:%M:%S", gmtime()))
        self.config = config
        self.__auth = HTTPBasicAuth(self.config['USER_EMAIL'], self.config['API_TOKEN'])
        self.start_jira_backup = 'https://{}/rest/backup/1/export/runbackup'.format(self.config['HOST_URL'])
        self.start_confluence_backup = 'https://{}/wiki/rest/obm/1.0/runbackup'.format(self.config['HOST_URL'])
        self.download_jira_backup = 'https://{}/plugins/servlet'.format(self.config['HOST_URL'])
        self.payload = {"cbAttachments": self.config['INCLUDE_ATTACHMENTS'], "exportToCloud": "true"}
        self.headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
        self.backup_status = {}
        self.wait = 30

    def create_confluence_backup(self):
        print('-> Starting backup; include attachments: {}'.format(self.config['INCLUDE_ATTACHMENTS']))
        session = requests.Session()
        session.auth = (self.config['USER_EMAIL'], self.config['API_TOKEN'])
        session.headers.update(self.headers)
        backup = session.post(self.start_confluence_backup, data=json.dumps(self.payload))
        if backup.status_code != 200:
            raise Exception(backup, backup.text)
        else:
            print('Backup process successfully started')
            progress_req = session.get('https://' + self.config['HOST_URL'] + '/wiki/rest/obm/1.0/getprogress')
            # print('debug: ' + progress_req.text)
            time.sleep(self.wait)
            while 'fileName' not in self.backup_status.keys():
                self.backup_status = json.loads(progress_req.text)
                print('debug: {}'.format(self.backup_status))
                time.sleep(2)

    def create_jira_backup(self):
        print('-> Starting backup; include attachments: {}'.format(self.config['INCLUDE_ATTACHMENTS']))
        backup = requests.post(self.start_jira_backup, data=json.dumps(self.payload), headers=self.headers, auth=self.__auth)
        if backup.status_code != 200:
            raise Exception(backup, backup.text)
        else:
            task_id = json.loads(backup.text)['taskId']
            print('Backup process successfully started: taskId={}'.format(task_id))
            URL_backup_progress = 'https://{jira_host}/rest/backup/1/export/getProgress?taskId={task_id}'.format(
                jira_host=self.config['HOST_URL'], task_id=task_id)
            time.sleep(self.wait)
            while 'result' not in self.backup_status.keys():
                self.backup_status = json.loads(requests.get(URL_backup_progress, auth=self.__auth).text)
                print('Current status: {status} {progress}; {description}'.format(
                    status=self.backup_status['status'], 
                    progress=self.backup_status['progress'], 
                    description=self.backup_status['description']))
                time.sleep(self.wait)
            return '{prefix}/{resultId}'.format(prefix=self.download_jira_backup, resultId=self.backup_status['result'])

    def download_file(self, url, local_filename):
        print('-> Downloading file from URL: {}'.format(url))
        r = requests.get(url, stream=True, auth=self.__auth)
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'backups', local_filename)
        with open(file_path, 'wb') as file_:
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    file_.write(chunk)
        print(file_path)

    def stream_to_s3(self, url, remote_filename):
        print('-> Streaming to S3')

        if self.config['UPLOAD_TO_S3']['AWS_ACCESS_KEY'] == '':
            connect = boto.connect_s3()
        else:
            connect = boto.connect_s3(
                aws_access_key_id=self.config['UPLOAD_TO_S3']['AWS_ACCESS_KEY'], 
                aws_secret_access_key=self.config['UPLOAD_TO_S3']['AWS_SECRET_KEY']
                )

        bucket = connect.get_bucket(self.config['UPLOAD_TO_S3']['S3_BUCKET'])
        r = requests.get(url, stream=True, auth=self.__auth)
        if r.status_code == 200:
            k = Key(bucket)
            k.key = remote_filename
            k.content_type = r.headers['content-type']
            k.set_contents_from_string(r.content)
            return


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-w', action='store_true', dest='wizard', help='activate config wizard')
    parser.add_argument('-c', action='store_true', dest='confluence', help='activate confluence backup')
    if parser.parse_args().wizard:
        wizard.create_config() 
    config = read_config()

    if config['HOST_URL'] == 'something.atlassian.net':
        raise ValueError('You forgated to edit config.json or to run the backup script with "-w" flag')

    atlass = Atlassian(config)
    if parser.parse_args().confluence: atlass.create_confluence_backup()
    else: backup_url = atlass.create_jira_backup()
    
    file_name = '{}.zip'.format(backup_url.split('/')[-1].replace('?fileId=', ''))
    
    if config['DOWNLOAD_LOCALLY'] == 'true':
        atlass.download_file(backup_url, file_name)  

    if config['UPLOAD_TO_S3']['S3_BUCKET'] != '':
        atlass.stream_to_s3(backup_url, file_name)
