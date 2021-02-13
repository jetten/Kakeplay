#!/usr/bin/python3

import traceback
import socket
import mysql.connector
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import subprocess

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from mpd import MPDClient

import tornado.ioloop
import tornado.web


load_dotenv()
MAX_VOLUME = 86     # Raspberry Pi 3 will produce distorted audio above 86% volume
playback_device_name = os.getenv("PLAYBACK_DEVICE_NAME")
debug = False


# Define global variables
spmask = None
queue = []


def spotify_login():
    #raise Exception("Spotify API disabled")

    global spmask
    if not spmask:
        print("Run spotify auth")
        spmask = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=os.getenv("SPOTIFY_CLIENT_ID"),
                                                           client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
                                                           redirect_uri="http://example.com",  # You do not have to change the example.com URL
                                                           scope="streaming,user-read-currently-playing,user-read-playback-state,user-read-playback-state",
                                                           open_browser=False))
    return spmask


def mpd_connect():
    mpdclient = MPDClient()
    mpdclient.connect(os.getenv("MPD_SERVER"), 6600)
    mpdclient.consume(1)   # Songs are removed from playlist after they played
    return mpdclient

# Return Spotify device id of the device where playback should happen
def get_playback_device_id():
    active_device_name = None
    playback_device_id = None
    for device in spotify_login().devices()['devices']:
        if(device['is_active']==True and device['name']!=playback_device_name):
            raise Exception("Uppspelning misslyckades: Spotify-konto i användning på: "+device['name'])

        if(device['name']==playback_device_name):
            playback_device_id = device['id']

    if not playback_device_id:
        raise Exception(playback_device_name+" ej inloggad på Spotify")

    return playback_device_id

# Return "mpd", "spotify" or "none" where playback is RUNNING
def get_playback_state():
    mpdclient = mpd_connect()
    mpdstatus = mpdclient.status()

    if mpdstatus['state'] == "play":
        return "mpd"
    else:
        current_playback = spotify_login().current_playback()
        if current_playback is not None:
            if(current_playback['device']['name']==playback_device_name and current_playback['is_playing']):
                return "spotify"

    return "none"


mysql_connection = None
def mysql_get_cursor():
    global mysql_connection
    try:
        mysql_connection.ping()
    except:
        print("Opening MySQL connection")
        mysql_connection = mysql.connector.connect(host=os.getenv("MYSQL_HOST"), user=os.getenv("MYSQL_USER"),
                                                   password=os.getenv("MYSQL_PASSWORD"), database=os.getenv("MYSQL_DATABASE"))

    return mysql_connection.cursor()


def get_song_cost(track_len):
    global queue
    if(len(queue) == 0):
        return 0
    elif (track_len >= 240):
        return 2
    else:
        return 1

def bill_query(query):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((os.getenv("BILLSERVER_HOST"), 4242))
        s.sendall( (query+"\n").encode("latin_1") )
        response = s.recv(1024)

        if(response != b".\n"):
            return response.decode("latin_1").partition("\n")[0] # Read response from BILL-API until "\n"
        else:
            return None

class BILLUser:
    def __init__(self,billcode,check_code=True):

        if (check_code==False):
            self.bill_key = billcode

        else:
            if not(billcode.isdigit() and len(billcode)>=6 and len(billcode)<=8):
                raise Exception("Kontrollera BILL-kod")

            self.bill_key = billcode[:-4]   # Allt utom fyra sista
            self.bill_code = billcode[-4:]  # Fyra sista

            response = bill_query('102,'+self.bill_key+',0,'+self.bill_code)
            if response:
                self.name = response
            else:
                raise Exception("Kontrollera BILL-kod")

    def get_credits(self):
        if self.bill_key and self.bill_key.isdigit() and len(self.bill_key)==4:
            response = bill_query('602,3,'+self.bill_key+',0,0')
        else:
            raise Exception("bill_key is invalid:"+str(self.bill_key))
        if response:
            return int(response)
        else:
            return 0

    def check_credit(self, change):
        credits = self.get_credits()
        return (credits-change >= 0)

    def consume_credit(self, change):
        if change == 0:
            return
        response = bill_query('702,3,'+self.bill_key+',0,0,'+str(-change))
        if not response:
            raise Exception("Debitering av kredit misslyckades")


class BaseHandler(tornado.web.RequestHandler):
    def get_current_user(self):
        return self.get_secure_cookie("user")

class MainHandler(BaseHandler):
    def get(self):
        if not self.current_user:
            self.render("login.html")
            return

        global playback_device_name
        bill_key = self.current_user.decode()
        name = self.get_secure_cookie("name").decode()
        self.render("index.html", playback_device_name=playback_device_name, user=bill_key, name=name, credits=BILLUser(bill_key,check_code=False).get_credits())

    def post(self):
        try:
            billuser = BILLUser(self.get_argument("billcode"))
        except Exception as e:
            self.write(str(e))
            return

        self.set_secure_cookie("user", billuser.bill_key)
        self.set_secure_cookie("name", billuser.name)
        self.redirect(".")

class LogoutHandler(BaseHandler):
    def get(self):
        self.clear_cookie("user")
        self.clear_cookie("name")
        self.redirect(".")

class SettingsHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        self.render("settings.html", user=self.get_secure_cookie("user"), name=self.get_secure_cookie("name"))



class PlayHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self, song_url):
        global queue
        playback_state = get_playback_state()

        bill_user = BILLUser(self.current_user.decode(),check_code=False)
        song_length = float(spotify_login().track("spotify:track:"+song_url)['duration_ms'])/1000
        if not bill_user.check_credit(get_song_cost(song_length)):
            self.write("Kreditteckning saknas")
            return

        try:
            if playback_state=="none":  # Start playback immediately
                spotify_login().start_playback(device_id=get_playback_device_id(), uris=["spotify:track:"+song_url])
                #queue=[(spotify_login().track("spotify:track:"+song_url))]
                #queue[-1]['in_queue'] = True

            else: # Already playing, add the track to queue
                #spotify_login().add_to_queue("spotify:track:"+song_url, device_id=get_playback_device_id())
                queue.append(spotify_login().track("spotify:track:"+song_url))
                queue[-1]['in_queue'] = False

            bill_user.consume_credit(get_song_cost(song_length))

        # Pass exception to user as it may contain relevant info such as "Spotify account in use elsewhere"
        except Exception as e:
            print(traceback.format_exc())
            self.write(str(e))

class MPDPlayHandler(BaseHandler):
    @tornado.web.authenticated
    def get(self):
        global queue
        song_url = self.get_argument('url', True)
        print(song_url)

        mpdclient = mpd_connect()
        song_info = mpdclient.listallinfo(song_url)[0]
        song_info['id'] = song_info['file']
        song_info['name'] = song_info['file'].split("/")[-1] # Remove directory part to get only filename
        song_info['album'] = {'images': [
                               {'url': "static/mp3_icon_600.png"},
                               {'url': "static/mp3_icon_600.png"},
                               {'url': "static/mp3_icon_64.png"}
                             ]}
        song_info['artists'] = [{'name': ""}]
        song_info['type'] = "mpd"
        song_info['in_queue'] = False

        bill_user = BILLUser(self.current_user.decode(),check_code=False)
        if not bill_user.check_credit(get_song_cost(float(song_info['duration']))):
            self.write("Kreditteckning saknas")
            return

        try:
            playback_state = get_playback_state()
            if playback_state=="spotify": # Already playing, what the f*** should I do now???
                #mpdclient.add(song_url)
                queue.append(song_info)
            elif playback_state=="mpd":
                #mpdclient.add(song_url)
                queue.append(song_info)
            else:                    # Start playback immediately
                mpdclient.add(song_url)
                mpdclient.play()
                #queue=[song_info]   # Remember to change in_queue=True if uncomment this!

            bill_user.consume_credit(get_song_cost(float(song_info['duration'])))

        # Pass exception to user as it may contain relevant info such as "Spotify account in use elsewhere"
        except Exception as e:
            print(traceback.format_exc())
            self.write(str(e))


class SearchHandler(tornado.web.RequestHandler):
    def post(self):
        if(self.request.body==b''):
            return
        try:
            results = spotify_login().search(q=self.request.body, limit=10)
        except Exception as e:
            self.set_status(500);
            self.write(str(e))
            return
        results_list = []
        for idx, track in enumerate(results['tracks']['items']):
            results_list.append( {
                "name": track['name'],
                "artist": track['artists'][0]['name'],
                "image": track['album']['images'], "id": track['id'],
                "credits": get_song_cost(int(track['duration_ms'])/1000)
            })
        self.write({"results": results_list})

class MPDSearchHandler(tornado.web.RequestHandler):
    def post(self):
        mpdclient = mpd_connect()
        results_list = []
        for idx, track in enumerate(mpdclient.search("filename", self.request.body.decode())):
            filename = track['file'].split("/")[-1] # Remove directory part to get only filename
            try:
                artist = track['artist']
            except:
                artist = ""

            results_list.append( {
                "name": filename,
                "artist": artist,
                "image": [{"url": "static/mp3_icon_600.png"}, {"url": "static/mp3_icon_600.png"}, {"url": "static/mp3_icon_64.png"}],
                "id": track['file'],
                "credits": get_song_cost(float(track['duration']))
            })

        self.write({"results": results_list})

class VolumeHandler(tornado.web.RequestHandler):
    def get(self, volume):
        spotify_login().volume(int(volume), device_id=get_playback_device_id())
        mpd_connect().setvol(int(int(volume)*0.8))

def get_pulseaudio_volume():
    result = subprocess.run(['pulsemixer', '--get-volume'], stdout=subprocess.PIPE)
    return int(result.stdout.decode().split(" ")[0])

def set_pulseaudio_volume(vol):
    vol = int(vol)
    if not vol in range(0, MAX_VOLUME+1):
        raise Exception("Volume "+str(vol)+" not inside range (0,"+str(MAX_VOLUME)+")")
    subprocess.run(['pulsemixer', '--set-volume', vol], stdout=subprocess.PIPE)

class CurrentHandler(tornado.web.RequestHandler):
    def get(self):

        mpdclient = mpd_connect()
        mpdstatus = mpdclient.status()


        if mpdstatus['state'] == "play":
            currentsong = mpdclient.currentsong()
            current_mpd_playback = {   # Build response for current MPD status compatible with Spotify's API response
                'device': {
                    'name': playback_device_name,
                    'is_active': True,
                    'volume_percent': int(min(float(mpdstatus['volume'])/0.8, 100)),
                    #'volume_percent': get_pulseaudio_volume(),
                },
                'progress_ms': int(float(mpdstatus['elapsed'])*1000),
                'item': {
                    'name': currentsong['file'].split("/")[-1],
                    'artists': [
                        {'name': ""}
                    ],
                    'album': {
                        'images': [
                            {'url': "static/mp3_icon_600.png"},
                            {'url': "static/mp3_icon_600.png"},
                            {'url': "static/mp3_icon_64.png"}
                        ]
                    },
                    'duration_ms': int(float(mpdstatus['duration'])*1000),
                    #'id': mpdstatus['songid'],
                    'id': currentsong['file'],
                    },
                'is_playing': True
            }
            self.write(current_mpd_playback)
        else:
            current_playback = spotify_login().current_playback()
            if current_playback is not None:
                #current_playback['device']['volume_percent'] = get_pulseaudio_volume()
                self.write(current_playback)
            else:
                #self.set_status(500)
                self.write("No playback running")
                return


class PauseHandler(tornado.web.RequestHandler):
    def get(self):
        spotify_login().pause_playback(device_id=get_playback_device_id())

class StartHandler(tornado.web.RequestHandler):
    def get(self):
        spotify_login().start_playback(device_id=get_playback_device_id())

class QueueHandler(tornado.web.RequestHandler):
    def get(self):
        global queue

        # current_playback = spotify_login().current_playback()
        # if (current_playback == None):
        #     queue = []
        # else:       # Remove already played songs from our list of the queue
        #     current_track_id = current_playback['item']['id']
        #     for i in range(len(queue)):
        #         if(queue[i]['id'] == current_track_id):
        #             queue = queue[i:]
        #             break

        queue_maintenance()
        self.write({"queue": queue})


last_queue_maintenance_run = datetime.min
def queue_maintenance():
    last_queue_maintenance_run_handle = tornado.ioloop.IOLoop.current().call_later(30, queue_maintenance)
    global last_queue_maintenance_run
    if(datetime.now()-last_queue_maintenance_run < timedelta(seconds=20)):
        print("queue_maintenance() skipping, last run", (datetime.now()-last_queue_maintenance_run).total_seconds(), "seconds ago")
        tornado.ioloop.IOLoop.current().remove_timeout(last_queue_maintenance_run_handle)
        return

    print("queue_maintenance() run, last run", (datetime.now()-last_queue_maintenance_run).total_seconds(), "seconds ago")
    last_queue_maintenance_run = datetime.now()

    #next_run_in = 30.0
    #tornado.ioloop.IOLoop.current().call_later(next_run_in, queue_maintenance)
    #print("  next run in", next_run_in)

    global queue
    mpdclient = mpd_connect()
    mpdstatus = mpdclient.status()

    # Case 1: mpd is playing
    if mpdstatus['state'] == "play":
        # Trim queue variable up until and including currently playing track
        current_track_id = mpdclient.currentsong()['file']
        for i in range(len(queue)):
            if(queue[i]['id'] == current_track_id):
                print("Cleared", i+1, "items from queue up until mpd id",current_track_id)
                queue = queue[i+1:]
                break

        # Time left of currently playing track
        time_left = float(mpdstatus['duration']) - float(mpdstatus['elapsed'])
        if (time_left < 45 and len(queue)!=0 and not queue[0]['in_queue']):
            #queue = queue[1:] # Get rid of currently playing track
            print("I will attempt at dealing with this piece of track:")
            print(queue[0])
            if (queue[0]['type']=="mpd"):     # mpd type
                mpdclient.add(queue[0]['id'])
            elif (queue[0]['type']=="track"): # Spotify type
                tornado.ioloop.IOLoop.current().call_later(time_left, spotify_start_playback_with, queue[0]['id'])
            queue[0]['in_queue'] = True


    else:
        current_playback = spotify_login().current_playback()

        # Case 2: Nothing is playing
        if (current_playback == None):
            if(len(queue)!=0 and not queue[0]['in_queue']):
                print("Detected no playback running, but", len(queue), "tracks in queue. Force-starting playback...")
                print("I will attempt at dealing with this piece of track:")
                print(queue[0])
                if (queue[0]['type']=="mpd"):     # mpd type
                    mpd_start_playback_with(queue[0]['id'])
                elif (queue[0]['type']=="track"): # Spotify type
                    spotify_start_playback_with(queue[0]['id'])
                queue[0]['in_queue'] = True
            else:
                # Put this app to sleep
                print("Putting queue_maintenance() to sleep")
                tornado.ioloop.IOLoop.current().remove_timeout(last_queue_maintenance_run_handle)

        # Case 3: Spotify is playing
        else:
            # Trim queue variable up until and including currently playing track
            current_track_id = current_playback['item']['id']
            for i in range(len(queue)):
                if(queue[i]['id'] == current_track_id):
                    print("Cleared", i+1, "items from queue up until spotify id",current_track_id)
                    queue = queue[i+1:]
                    break

            # Time left of currently playing track
            time_left = float(current_playback['item']['duration_ms'] - current_playback['progress_ms']) / 1000
            if (time_left < 30 and len(queue)!=0 and not queue[0]['in_queue']):
                #queue = queue[1:] # Get rid of currently playing track
                print("I will attempt at dealing with this piece of track:")
                print(queue[0])
                if (queue[0]['type']=="mpd"):     # mpd type
                    tornado.ioloop.IOLoop.current().call_later(time_left, mpd_start_playback_with, queue[0]['id'])
                elif (queue[0]['type']=="track"): # Spotify type
                    spotify_login().add_to_queue("spotify:track:"+queue[0]['id'], device_id=get_playback_device_id())

def spotify_start_playback_with(song_url):
    spotify_login().start_playback(device_id=get_playback_device_id(), uris=["spotify:track:"+song_url])

def mpd_start_playback_with(song_url):
    mpdclient = mpd_connect()
    mpdclient.add(song_url)
    mpdclient.play()

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/jquery-3.5.1.min.js()", tornado.web.StaticFileHandler, {'path': 'jquery-3.5.1.min.js'}),
        (r"/bootstrap-4.5.3-dist/(.*)", tornado.web.StaticFileHandler, {'path': 'bootstrap-4.5.3-dist'}),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': 'static'}),
        (r"/logout", LogoutHandler),
        (r"/play_track/(.*)", PlayHandler),
        (r"/search", SearchHandler),
        (r"/volume/(.*)", VolumeHandler),
        (r"/current", CurrentHandler),
        (r"/play", StartHandler),
        (r"/pause", PauseHandler),
        (r"/queue", QueueHandler),
        (r"/mpdsearch", MPDSearchHandler),
        (r"/mpd_play_track", MPDPlayHandler),
        (r"/settings", SettingsHandler),
    ],
    debug = debug,
    cookie_secret = os.getenv("TORNADO_COOKIE_SECRET"),
    login_url = "."
    )

if __name__ == "__main__":
    app = make_app()
    app.listen(8888, "localhost")
    #tornado.ioloop.IOLoop.current().call_later(5.0, queue_maintenance)

    tornado.ioloop.IOLoop.current().start()
