# Kakeplay
A jukebox for Spotify and MPD

## What can it do
The app provides a web-based interface where your users can search for songs in Spotify and your local MPD library and put them in your playback queue. Songs will be queued using Spotify Connect on a Spotify Premium account of your choice, where they will be played back in the order they were queued.

## Requirements
- Python 3
- Spotify Premium account
- Spotify for Developers app ID
- MPD server configured with a local MP3 library
- BILL server for storing user's profiles

## How to use it
- Set the variables related to **Spotify**, **BILL**, **MPD**, and **Tornado** and in the `.env` file.
- Launch the official Spotify app and sign in with your premium account on the device where playback will happen.
- Install requirements: `pip install -r requirements.txt`
- Run the app: `python3 main.py`
- On the first run the app will display an URL which asks you to sign in to Spotify. Use the account logged in on the official Spotify app.
- Access http://localhost:8888 with your web browser
