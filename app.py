import json

import requests
from flask import Flask, redirect, url_for, session, request, render_template, jsonify, flash
from requests_oauthlib import OAuth1Session
import os

app = Flask(__name__)
app.secret_key = 'your_secret_key'

# Twitter API credentials for OAuth 1.0
CONSUMER_KEY = ''
CONSUMER_SECRET = ''
REQUEST_TOKEN_URL = 'https://api.twitter.com/oauth/request_token'
AUTHORIZATION_URL = 'https://api.twitter.com/oauth/authorize'
ACCESS_TOKEN_URL = 'https://api.twitter.com/oauth/access_token'

@app.route('/twitter-image/')
def home():
    is_logged_in = 'oauth_token' in session
    return render_template('index.html', is_logged_in=is_logged_in)

@app.route('/twitter-image/login')
def login():
    # Step 1: Obtain a request token
    oauth = OAuth1Session(CONSUMER_KEY, client_secret=CONSUMER_SECRET, callback_uri='http://myapp.local:5000/twitter-image/callback')
    fetch_response = oauth.fetch_request_token(REQUEST_TOKEN_URL)
    session['oauth_token'] = fetch_response.get('oauth_token')
    session['oauth_token_secret'] = fetch_response.get('oauth_token_secret')

    # Step 2: Redirect the user to Twitter's authorization page
    authorization_url = oauth.authorization_url(AUTHORIZATION_URL)
    return redirect(authorization_url)

@app.route('/twitter-image/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/twitter-image/callback')
def callback():
    # Retrieve the request token and verifier from the callback URL
    oauth_token = request.args.get('oauth_token')
    oauth_verifier = request.args.get('oauth_verifier')

    # Step 3: Obtain the access token using the verifier
    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=session.get('oauth_token'),
        resource_owner_secret=session.get('oauth_token_secret'),
        verifier=oauth_verifier
    )
    oauth_tokens = oauth.fetch_access_token(ACCESS_TOKEN_URL)
    session['oauth_token'] = oauth_tokens.get('oauth_token')
    session['oauth_token_secret'] = oauth_tokens.get('oauth_token_secret')

    return redirect(url_for('home'))

@app.route('/twitter-image/post_image_tweet', methods=['POST'])
def post_image_tweet():
    if 'oauth_token' not in session or 'oauth_token_secret' not in session:
        return jsonify({"error": "User not authenticated"}), 403

    # Set up OAuth session with access token
    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=session['oauth_token'],
        resource_owner_secret=session['oauth_token_secret']
    )

    # Step 1: Upload media
    image_file = request.files['image']
    files = {'media': image_file}
    upload_response = oauth.post("https://upload.twitter.com/1.1/media/upload.json", files=files)
    upload_data = upload_response.json()

    if upload_response.status_code != 200 or 'media_id_string' not in upload_data:
        return jsonify({"error": "Media upload failed", "details": upload_data}), 400

    media_id = upload_data['media_id_string']

    # Step 2: Post tweet with media
    tweet_text = request.form.get('text', 'This is a test tweet with an image!')
    tweet_url = "https://api.twitter.com/2/tweets"
    tweet_headers = {
        'Content-Type': 'application/json'
    }
    tweet_payload = {
        'text': tweet_text,
        'media': {
            'media_ids': [media_id]
        }
    }

    tweet_response = oauth.post(tweet_url, json=tweet_payload, headers=tweet_headers)
    # tweet_data = tweet_response.json() use later for getting tweet id or something

    if tweet_response.status_code == 201:
        flash("Tweet posted successfully!", "success")
        tweet_data = tweet_response.json()
        tweet_id = tweet_data["data"]["id"]
        save_tweet_id(tweet_id)
    else:
        flash("Failed to post tweet.", "error")

    return redirect(url_for('home'))


def get_tweet_data(tweet_id):
    api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=token"
    try:
        response = requests.get(api_url)
        response.raise_for_status()  # Raise an HTTPError for bad responses (4xx or 5xx)
        tweet_data = response.json()

        # Check if essential fields are present, else consider it an incomplete or deleted tweet
        if "id_str" in tweet_data and "user" in tweet_data and "favorite_count" in tweet_data:
            return {
                "id": tweet_data["id_str"],
                "name": tweet_data["user"]["name"],
                "favorite_count": tweet_data.get("favorite_count", 0)
            }
        else:
            # If required fields are missing, log a message and skip this tweet
            print(f"Incomplete tweet data for ID {tweet_id}, skipping.")
            return None
    except requests.exceptions.RequestException as e:
        # Log the error and skip this tweet if there’s a request error
        print(f"Error fetching data for tweet ID {tweet_id}: {e}")
        return None


# Route to render the dashboard data
@app.route('/twitter-image/dashboard')
def dashboard():
    tweets = []
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, 'r') as file:
            tweets_data = json.load(file)
            for tweet in tweets_data["tweets"]:
                tweet_info = get_tweet_data(tweet["id"])
                if tweet_info:
                    tweets.append(tweet_info)
    return jsonify(tweets)


TWEETS_FILE = 'tweets.json'


def save_tweet_id(tweet_id):
    # Load existing tweet data from the file
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, 'r') as file:
            tweets_data = json.load(file)
    else:
        tweets_data = {"tweets": []}

    # Append the new tweet ID and text to the data
    tweets_data["tweets"].append({"id": tweet_id})

    # Write the updated data back to the file
    with open(TWEETS_FILE, 'w') as file:
        json.dump(tweets_data, file, indent=2)


def get_all_tweet_ids():
    # Read all tweet IDs from the JSON file
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, 'r') as file:
            tweets_data = json.load(file)
        return [tweet["id"] for tweet in tweets_data["tweets"]]
    return []


if __name__ == '__main__':
    app.run(debug=True, port=5000)
