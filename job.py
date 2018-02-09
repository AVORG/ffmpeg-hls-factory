# -*- coding: utf-8 -*-
import ConfigParser, logging,urllib, json, subprocess
import boto,os, shutil

class Job(object):

    def __init__(self):

        config = ConfigParser.ConfigParser()
        config.read('/home/ec2-user/settings.ini')

        self.id = 0
        self.status = 'Unknown'
        self.fileName = ''
        self.downloadPath = ''
        self.downloadHostname = ''
        self.destinationURL = ''
        self.ffmpeg = config.get('Encoder', 'ffmpeg')
        self.ffprobe = config.get('Encoder', 'ffprobe')
        self.ffprobe_params = config.get('Encoder', 'ffprobe_params')
        self.audio_encoder = config.get('Encoder', 'audio_encoder')
        self.hls_config = {
            '64': {
                'width': 0,
                'profile': config.get('Encoder', 'hls_audio'),
                'bandwidth': config.get('Encoder', 'audio_bandwidth'),
                'name': config.get('Encoder', 'audio_name')
            },
            '240': {
                'width': 352,
                'profile': config.get('Encoder', 'hls_cell'),
                'bandwidth': config.get('Encoder', 'cell_bandwidth'),
                'name': config.get('Encoder', 'cell_name')
            },
            '360': {
                'width': 640,
                'profile': config.get('Encoder', 'hls_wifi_360'),
                'bandwidth': config.get('Encoder', 'wifi_360_bandwidth'),
                'name': config.get('Encoder', 'wifi_360_name')
            },
            '720': {
                'width': 1280,
                'profile': config.get('Encoder', 'hls_wifi_720'),
                'bandwidth': config.get('Encoder', 'wifi_720_bandwidth'),
                'name': config.get('Encoder', 'wifi_720_name')
            },
            '1080': {
                'width': 1920,
                'profile': config.get('Encoder', 'hls_wifi_1080'),
                'bandwidth': config.get('Encoder', 'wifi_1080_bandwidth'),
                'name': config.get('Encoder', 'wifi_1080_name')
            }
        }
        self.mp4_config = {
            '240': {
                'width': 352,
                'profile': config.get('Encoder', 'mp4_240')
            },
            '360': {
                'width': 640,
                'profile': config.get('Encoder', 'mp4_360')
            },
            '720': {
                'width': 1280,
                'profile': config.get('Encoder', 'mp4_720')
            },
            '1080': {
                'width': 1920,
                'profile': config.get('Encoder', 'mp4_1080')
            }
        }
        self.output_dir_hls = config.get('Encoder', 'output_dir_hls')
        self.remote_dir_hls = config.get('Encoder', 'remote_dir_hls')
        self.output_dir_mp4 = config.get('Encoder', 'output_dir_mp4')
        self.s3_bucket = config.get('AWS_S3', 'Bucket')
        self.s3_access = config.get('AWS_S3', 'ACCESS_KEY_ID')
        self.s3_secret = config.get('AWS_S3', 'SECRET_ACCESS_KEY')
        self.ios_playlist = ''
        self.web_playlist = ''
        self.mp4_file_name = ''
        # if the output directory does not exists, create one
        if not os.path.exists(self.output_dir_hls):
            os.makedirs(self.output_dir_hls)

        if not os.path.exists(self.output_dir_mp4):
            os.makedirs(self.output_dir_mp4)

    def download_file(self):

        opener = urllib.URLopener()
        try:
            full_path = self.downloadHostname + self.downloadPath + self.fileName
            logging.info("Job downloading %s from %s" % (self.fileName, full_path))
            opener.retrieve(full_path.encode('utf-8'), self.fileName)

        except IOError as e:
            logging.warning(e)
            raise Exception('DOWNLOAD FILE: Error: ' + e)

    def generate_hls(self, api):

        logging.info('GENERATE HLS: START')
        media_info = self.probe_media_file(self.fileName)
        width = 1920

        if 'width' in media_info:
            width = int(media_info['width'])

        for key in sorted(self.hls_config):

            if width >= self.hls_config[key]['width']:
                logging.info('GENERATE HLS: generating %s' % self.hls_config[key]['width'])
                cmd = (self.hls_config[key]['profile'] % (
                    self.ffmpeg,
                    self.fileName,
                    self.audio_encoder,
                    self.output_dir_hls+key)
                ).split()
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = p.communicate()
                #print out, err
            else:
                logging.info('GENERATE HLS: skipping %s (input movie is %s)' % (key, width))
        # generate index m3u8
        self.write_ios_playlist(api)
        self.write_web_playlist(api)
        logging.info('GENERATE HLS: END')

    def generate_mp4(self, api):

        logging.info('GENERATE MP4: Begin')
        media_info = self.probe_media_file(self.fileName)
        width = 1920
        self.mp4_file_name, file_extension = os.path.splitext(self.fileName)
        file_extension = '.mp4'

        if 'width' in media_info:
            width = int(media_info['width'])

        for key in self.mp4_config:

            if width >= self.mp4_config[key]['width']:
                logging.info('GENERATE MP4: generating %s' % (key))
                cmd = (self.mp4_config[key]['profile'] % (
                    self.ffmpeg,
                    self.fileName,
                    self.audio_encoder,
                    self.output_dir_mp4+self.mp4_file_name+'_'+key)
                ).split()

                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                out, err = p.communicate()

                if p.returncode:
                    logging.info('GENERATE MP4: ffmpeg failed out %s err %s' % (out, err))
                else:
                    logging.info('GENERATE MP4: check in')
                    file_path = self.output_dir_mp4 + self.mp4_file_name + '_' + key + file_extension
                    media_info = self.probe_media_file(file_path)
                    api.checkin_flavor({
                        'recordingId': self.recordingId,
                        'filename': unicode(self.mp4_file_name + '_' + key + file_extension).encode('utf-8'),
                        'filesize': os.path.getsize(file_path),
                        'duration': round(float(media_info['duration']), 1),
                        'bitrate': media_info['bit_rate'],
                        'width': media_info['width'],
                        'height': media_info['height'],
                        'container': 'mp4'
                    })
            else:
                logging.info('GENERATE MP4: Skipping %s (input movie is %s)' % (key, width))

        logging.info('GENERATE MP4: End')

    def write_ios_playlist(self, api):

        logging.info('WRITE IOS PLAYLIST: BEGIN')
        media_info = self.probe_media_file(self.fileName)
        width = 1920

        if 'width' in media_info:
            width = int(media_info['width'])

        file_name, file_extension = os.path.splitext(self.fileName)
        self.ios_playlist = file_name + ".m3u8"

        f = open(self.ios_playlist, 'w')
        f.write('#EXTM3U\n')

        for key in sorted(self.hls_config):
            if width >= self.hls_config[key]['width']:
                f.write('#EXT-X-STREAM-INF:PROGRAM-ID=1,NAME="%s",BANDWIDTH=%s\n'%(self.hls_config[key]['name'], self.hls_config[key]['bandwidth']))
                f.write(self.remote_dir_hls+key+'_.m3u8\n')

        f.close()
        logging.info('WRITE IOS PLAYLIST: checkin flavor')
        api.checkin_flavor({
            'recordingId': self.recordingId,
            'filename': unicode(self.ios_playlist).encode('utf-8'),
            'filesize': 0,
            'duration': round(float(media_info['duration']), 1),
            'bitrate': media_info['bit_rate'],
            'width': media_info['width'],
            'height': media_info['height'],
            'container': 'm3u8_ios'
        })
        logging.info('WRITE IOS PLAYLIST: ios playlist %s generated'%(self.ios_playlist))

    def write_web_playlist(self, api):

        media_info = self.probe_media_file(self.fileName)
        width = 1920

        if 'width' in media_info:
            width = int(media_info['width'])

        file_name, file_extension = os.path.splitext(self.fileName)
        self.web_playlist = file_name + "_web.m3u8"

        f = open(self.web_playlist, 'w')
        f.write('#EXTM3U\n')

        for key in sorted(self.hls_config):
            # omitt audio
            if int(key) != 64 and width >= self.hls_config[key]['width']:
                f.write('#EXT-X-STREAM-INF:PROGRAM-ID=1,NAME="%s",BANDWIDTH=%s\n'%(self.hls_config[key]['name'], self.hls_config[key]['bandwidth']))
                f.write(self.remote_dir_hls+key+'_.m3u8\n')

        f.close()

        api.checkin_flavor({
            'recordingId': self.recordingId,
            'filename': unicode(self.web_playlist).encode('utf-8'),
            'filesize': 0,
            'duration': round(float(media_info['duration']), 1),
            'bitrate': media_info['bit_rate'],
            'width': media_info['width'],
            'height': media_info['height'],
            'container': 'm3u8_web'
        })

        logging.info('GENERATE HLS:: web playlist %s generated'%(self.web_playlist))

    def transfer_S3(self):
        # destination directory name (on s3)
        upload_file_names = []
        try:
            logging.info('S3 TRANSFER: uploading files to bucket %s' % (self.s3_bucket))
            conn = boto.connect_s3(self.s3_access,self.s3_secret)
            bucket = conn.get_bucket(self.s3_bucket)
            logging.info('S3 TRANSFER: HLS PREPARE DATA')
            # Transfer HLS
            for (self.output_dir_hls, dirname, filename) in os.walk(self.output_dir_hls):
                upload_file_names.extend(filename)
                break

            logging.info('S3 TRANSFER: HLS UPLOAD DATA')
            for filename in upload_file_names:
                source_path = os.path.join(self.output_dir_hls + filename)
                dest_path = os.path.join(self.destinationURL + self.remote_dir_hls, filename)

                k = boto.s3.key.Key(bucket)
                k.key = dest_path
                k.set_contents_from_filename(source_path)
                k.set_acl('public-read')

            logging.info('S3 TRANSFER: MP4 PREPARE DATA')
            # Transfer MP4
            upload_file_names = []
            for (self.output_dir_mp4, dirname, filename) in os.walk(self.output_dir_mp4):
                upload_file_names.extend(filename)
                break

            logging.info('S3 TRANSFER: MP4 UPLOAD DATA')
            for filename in upload_file_names:
                source_path = os.path.join(self.output_dir_mp4 + filename)
                dest_path = os.path.join(self.destinationURL, filename)

                k = boto.s3.key.Key(bucket)
                k.key = dest_path
                k.set_contents_from_filename(source_path)
                k.set_acl('public-read')

            logging.info('S3 TRANSFER: IOS PLAYLIST')
            # Upload index playlist
            k = boto.s3.key.Key(bucket)
            k.key = os.path.join(self.destinationURL, self.ios_playlist)
            k.set_contents_from_filename(os.path.join(self.ios_playlist))
            k.set_acl('public-read')

            logging.info('S3 TRANSFER: IOS WEB PLAYLIST')
            k = boto.s3.key.Key(bucket)
            k.key = os.path.join(self.destinationURL, self.web_playlist)
            k.set_contents_from_filename(os.path.join(self.web_playlist))
            k.set_acl('public-read')
            # update job status
            self.status = 'OK'

        except boto.exception.S3ResponseError as e:
            # 403 Forbidden, 404 Not Found
            logging.error(e)
            raise Exception('S3 TRANSFER: error: ' + e)

        logging.info('S3 TRANSFER END')

    def probe_media_file(self, fileName):

        media_info = {}
        cmd = (self.ffprobe_params % (self.ffprobe, fileName)).split()
        logging.info('MEDIA PROBE: Probing %s' % fileName)
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate()
        #print out
        for line in out.split(os.linesep):
            if line.strip():
                name, value = line.partition("=")[::2]
                # ffprobe sometime returns many of the same values
                if name.strip() not in media_info:
                    if ( value == 'N/A'):
                        value = 0
                    media_info[name.strip()] = value
        logging.info('MEDIA PROBE: END');
        return media_info
        
    def cleanup(self):
        logging.info('Job: Cleaning up')
        # delete HLS directory with all of its contents
        shutil.rmtree(self.output_dir_hls)
        # delete MP4 directory with all of its contents
        shutil.rmtree(self.output_dir_mp4)
        # the exceptions were added in the case that the files doesn't exist
        
        try:
            os.remove(self.ios_playlist)
	    except OSError:
            pass
        
        try:
            os.remove(self.web_playlist)
        except OSError:
            pass
        
        try:
            os.remove(self.fileName)
        except OSError:
            pass

    def __str__(self):
        print self.id, self.status, self.fileName, self.downloadPath, self.downloadHostname, self.output_dir_hls
