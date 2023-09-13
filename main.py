#!/usr/bin/python3

import traceback
import socket
import mysql.connector
from datetime import datetime, timedelta
from dotenv import load_dotenv
import os
import subprocess
import html
import geoip2.database

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials, SpotifyOAuth

from mpd import MPDClient

import tornado.ioloop
import tornado.web


load_dotenv()
playback_device_name = os.getenv("SPOTIFY_PLAYBACK_DEVICE_NAME")
debug = False


# Define global variables
spmask = None
queue = []
lastlogin = {}


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


def get_song_cost(track_len, bill_user):
    global queue
    if(len(queue) == 0):
        return 0
    elif (bill_user.is_admin):
        return 0
    elif (track_len >= 300):
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
            if not(billcode and billcode.isdigit() and len(billcode)>=2 and len(billcode)<=4):
                print("Invalid bill_key:", str(self.bill_key))
                raise Exception("bill_key read from secure cookie is not valid!")
            self.bill_key = billcode

        else:
            if not(billcode.isdigit() and len(billcode)>=6 and len(billcode)<=8):
                raise Exception("Kontrollera BILL-kod")

            self.bill_key = billcode[:-4]   # Allt utom fyra sista
            self.bill_code = billcode[-4:]  # Fyra sista

            response = bill_query('102,'+self.bill_key+',0,'+self.bill_code)
            if response:
                # Use html.unescape to convert characters not compatible with latin_1 to utf8
                self.name = html.unescape(response)
            else:
                raise Exception("Kontrollera BILL-kod")

        # Check if user is admin
        self.is_admin = False
        if(os.path.exists('adminkeys')):
            with open('adminkeys', 'r') as f:
                for line in f:
                    if line[:-1]==self.bill_key:
                        self.is_admin = True
                        break


    def get_credits(self):
        response = bill_query('602,3,'+self.bill_key+',0,0')
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
        bill_user = BILLUser(self.current_user.decode(),check_code=False)
        name = self.get_secure_cookie("name").decode()
        self.render("index.html", playback_device_name=playback_device_name, user=bill_key, name=name, admin=bill_user.is_admin, credits=BILLUser(bill_key,check_code=False).get_credits())

    def post(self):
        try:
            with geoip2.database.Reader('/var/lib/GeoIP/GeoLite2-Country.mmdb') as reader:
                remote_ip = self.request.headers.get("X-Forwarded-For") or self.request.remote_ip
                response = reader.country(remote_ip)
                client_country = response.country.iso_code
                if (client_country != "FI"):
                    print("GeoIP rejected login from IP", remote_ip, "("+client_country+")")
                    self.write("Din IP-adress är inte godkänd för att använda denna tjänst.")
                    return
                print("User logged in from IP", remote_ip, "("+client_country+")")
        except Exception as e:
            print(traceback.format_exc())

        try:
            remote_ip = self.request.headers.get("X-Forwarded-For") or self.request.remote_ip
            if lastlogin[remote_ip] + timedelta(seconds=3) > datetime.now():
                #self.set_status(429)  # 429 Too Many Requests
                self.write("Kontrollera BILL-kod")
                return
        except:
            pass
        finally:
            lastlogin[remote_ip] = datetime.now()

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
        if not bill_user.check_credit(get_song_cost(song_length,bill_user)):
            self.write("Kreditteckning saknas")
            return

        try:
            if playback_state=="none":  # Start playback immediately
                spotify_start_playback_with(song_url)
                #queue=[(spotify_login().track("spotify:track:"+song_url))]
                #queue[-1]['in_queue'] = True

            else: # Already playing, add the track to queue
                #spotify_login().add_to_queue("spotify:track:"+song_url, device_id=get_playback_device_id())
                queue.append(spotify_login().track("spotify:track:"+song_url))
                queue[-1]['in_queue'] = False

            bill_user.consume_credit(get_song_cost(song_length,bill_user))

        # Pass exception to user as it may contain relevant info such as "Spotify account in use elsewhere"
        except Exception as e:
            print(e)
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
        if not bill_user.check_credit(get_song_cost(float(song_info['duration']),bill_user)):
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

            bill_user.consume_credit(get_song_cost(float(song_info['duration']),bill_user))

        # Pass exception to user as it may contain relevant info such as "Spotify account in use elsewhere"
        except Exception as e:
            print(e)
            self.write(str(e))


class DeleteHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        global queue
        song_id = self.get_argument('id')
        print("Attempting to delete", song_id, "from playlist.")

        for i in range(len(queue)):
            if(queue[i]['id'] == song_id):
                queue.pop(i)
                break

class SearchHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        bill_user = BILLUser(self.current_user.decode(),check_code=False)

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
                "credits": get_song_cost(int(track['duration_ms'])/1000, bill_user)
            })
        self.write({"results": results_list})

class MPDSearchHandler(BaseHandler):
    @tornado.web.authenticated
    def post(self):
        bill_user = BILLUser(self.current_user.decode(),check_code=False)

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
                "credits": get_song_cost(float(track['duration']), bill_user)
            })

        self.write({"results": results_list})

class VolumeHandler(tornado.web.RequestHandler):
    def get(self, volume):
        try:
            spotify_login().volume(int(volume), device_id=get_playback_device_id())
        except:
            pass
        mpd_connect().setvol(int(volume))


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
                    'volume_percent': int(mpdstatus['volume']),
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
                self.write(current_playback)
            else:
                #self.set_status(500)
                self.write("No playback running")
                return


class MediaControlHandler(MainHandler):
    @tornado.web.authenticated

    def post(self):
        global current_track_type
        try:
            current_track_type
        except:
            current_track_type = None

        print("current_track_type:", current_track_type)

        if current_track_type == "spotify":
            if  (self.get_argument('action') == 'play'):
                spotify_login().start_playback(device_id=get_playback_device_id())
            elif(self.get_argument('action') == 'pause'):
                spotify_login().pause_playback(device_id=get_playback_device_id())
            elif(self.get_argument('action') == 'prev'):
                spotify_login().previous_track(device_id=get_playback_device_id())
            elif(self.get_argument('action') == 'next'):
                pass
                #spotify_login().next_track(device_id=get_playback_device_id())
        if current_track_type == "mpd":
            mpdclient = mpd_connect()
            if  (self.get_argument('action') == 'play'):
                mpdclient.pause(0)
            elif(self.get_argument('action') == 'pause'):
                mpdclient.pause(1)
            elif(self.get_argument('action') == 'prev'):
                mpdclient.previous()


class QueueHandler(tornado.web.RequestHandler):
    def get(self):
        global queue, queue_maintenance_needed
        queue_maintenance_needed = True
        queue_maintenance()
        self.write({"queue": queue})


last_queue_maintenance_run = datetime.min
queue_maintenance_needed = False
def queue_maintenance():
    #last_queue_maintenance_run_handle = tornado.ioloop.IOLoop.current().call_later(30, queue_maintenance)
    global last_queue_maintenance_run
    global queue_maintenance_needed

    if not queue_maintenance_needed:
        return

    if(datetime.now()-last_queue_maintenance_run < timedelta(seconds=25)):
        print("queue_maintenance() skipping, last run", (datetime.now()-last_queue_maintenance_run).total_seconds(), "seconds ago")
        return

    print("queue_maintenance() run, last run", (datetime.now()-last_queue_maintenance_run).total_seconds(), "seconds ago")
    last_queue_maintenance_run = datetime.now()

    global queue
    global current_track_type
    mpdclient = mpd_connect()
    mpdstatus = mpdclient.status()

    # Case 1: mpd is playing
    if mpdstatus['state'] == "play":
        # Trim queue variable up until and including currently playing track
        current_track_id = mpdclient.currentsong()['file']
        current_track_type = "mpd"
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
                print("Spotify playback queued in future: IOLoop.call_later("+str(time_left)+", spotify_start_playback_with, "+str(queue[0]['id'])+")")
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
                #tornado.ioloop.IOLoop.current().remove_timeout(last_queue_maintenance_run_handle)
                queue_maintenance_needed = False


        # Case 3: Spotify is playing
        else:
            try:
                get_playback_device_id()
            except Exception as e:
                print("Spotify-konto ej tillgängligt")
                print("Putting queue_maintenance() to sleep")
                queue_maintenance_needed = False
                return

            # Trim queue variable up until and including currently playing track
            current_track_id = current_playback['item']['id']
            current_track_type = "spotify"
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
                    print("MPD playback queued in future: IOLoop.call_later("+str(time_left)+", mpd_start_playback_with, "+str(queue[0]['id'])+")")
                    tornado.ioloop.IOLoop.current().call_later(time_left, mpd_start_playback_with, queue[0]['id'])
                elif (queue[0]['type']=="track"): # Spotify type
                    spotify_login().add_to_queue("spotify:track:"+queue[0]['id'], device_id=get_playback_device_id())

def spotify_start_playback_with(song_url):
    print("Running spotify_start_playback_with("+str(song_url)+")")
    playback_device_id = get_playback_device_id()
    spotify_login().repeat("off", playback_device_id)
    spotify_login().shuffle("off", playback_device_id)
    spotify_login().start_playback(device_id=get_playback_device_id(), uris=["spotify:track:"+song_url])

def mpd_start_playback_with(song_url):
    print("Running mpd_start_playback_with("+str(song_url)+")")
    mpdclient = mpd_connect()
    mpdclient.add(song_url)
    mpdclient.play()

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {'path': 'static'}),
        (r"/logout", LogoutHandler),
        (r"/play_track/(.*)", PlayHandler),
        (r"/delete_track", DeleteHandler),
        (r"/search", SearchHandler),
        (r"/volume/(.*)", VolumeHandler),
        (r"/current", CurrentHandler),
        (r"/mediacontrol", MediaControlHandler),
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
    tornado.ioloop.PeriodicCallback(queue_maintenance, 30000).start()

    tornado.ioloop.IOLoop.current().start()
