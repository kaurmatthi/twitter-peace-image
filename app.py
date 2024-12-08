import json
import os
from io import BytesIO
from datetime import datetime, timedelta, timezone
import requests
from flask import Flask, redirect, url_for, session, request, render_template, jsonify
from flask_cors import CORS
from requests_oauthlib import OAuth1Session
import jwt
import openai

# Set OpenAI API key
openai.api_key = ''

# Flask app setup with CORS support, session security and JWT configuration
app = Flask(__name__)
CORS(app, origins=["http://localhost:5173"], supports_credentials=True, methods=["GET", "POST", "OPTIONS"])
app.secret_key = 'your_secret_key'
JWT_SECRET = "your_jwt_secret_key"
JWT_ALGORITHM = "HS256"

# Twitter API credentials for OAuth 1.0
CONSUMER_KEY = ''
CONSUMER_SECRET = ''
REQUEST_TOKEN_URL = 'https://api.twitter.com/oauth/request_token'
AUTHORIZATION_URL = 'https://api.twitter.com/oauth/authorize'
ACCESS_TOKEN_URL = 'https://api.twitter.com/oauth/access_token'

# File to store tweet IDs
TWEETS_FILE = 'tweets.json'

@app.route('/twitter-image/')
def home():
    is_logged_in = 'oauth_token' in session
    return render_template('index.html', is_logged_in=is_logged_in)

@app.route('/twitter-image/login')
def login():
    # Step 1: Obtain a request token
    oauth = OAuth1Session(CONSUMER_KEY, client_secret=CONSUMER_SECRET, callback_uri='http://127.0.0.1:5000/twitter-image/callback')
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
    oauth = OAuth1Session(
        CONSUMER_KEY,
        client_secret=CONSUMER_SECRET,
        resource_owner_key=session.get('oauth_token'),
        resource_owner_secret=session.get('oauth_token_secret'),
        verifier=request.args.get('oauth_verifier')
    )
    tokens = oauth.fetch_access_token(ACCESS_TOKEN_URL)

    # Generate JWT with OAuth tokens
    jwt_payload = {
        "oauth_token": tokens.get("oauth_token"),
        "oauth_token_secret": tokens.get("oauth_token_secret"),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1)  # Token expires in 1 hour
    }
    jwt_token = jwt.encode(jwt_payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    # Redirect to frontend with authToken
    frontend_redirect_url = f"http://localhost:5173/?authToken={jwt_token}"
    return redirect(frontend_redirect_url)


@app.route('/twitter-image/generate_image', methods=['POST'])
def generate_image():
    try:
        auth_header = request.headers.get("Authorization")
        validate_jwt(auth_header)

        request_data = request.get_json()
        prompt = request_data.get("prompt")
        if not prompt:
            return jsonify({"error": "Prompt is required"}), 400

        # Use the OpenAI Image API to generate an image
        response = openai.Image.create(
            prompt=prompt,
            n=1,
            size="1024x1024",
            model="dall-e-3"
        )

        # Extract the URL of the generated image
        generated_image_url = response['data'][0]['url']

        return jsonify({"imageUrl": generated_image_url}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    

@app.route('/twitter-image/post_image_tweet', methods=['POST'])
def post_image_tweet():
    # Step 1: Validate JWT
    auth_header = request.headers.get("Authorization")
    oauth_token, oauth_token_secret = validate_jwt(auth_header)

    # Step 2: Parse request data
    request_data = request.get_json()
    image_url = request_data.get("imageUrl")
    tweet_text = request_data.get("prompt")
    if not image_url:
        return jsonify({"error": "imageUrl is required"}), 400
    if not tweet_text:
        return jsonify({"error": "prompt is required"}), 400

    # Step 3: Upload the image to Twitter
    oauth = OAuth1Session(
        CONSUMER_KEY, client_secret=CONSUMER_SECRET,
        resource_owner_key=oauth_token, resource_owner_secret=oauth_token_secret
    )
    upload_response = oauth.post(
        "https://upload.twitter.com/1.1/media/upload.json",
        files={"media": download_image(image_url)}
    )
    if upload_response.status_code != 200:
        return jsonify({"error": "Failed to upload media", "details": upload_response.json()}), 400

    media_id = upload_response.json().get("media_id_string")
    if not media_id:
        return jsonify({"error": "No media_id returned from Twitter"}), 400

    # Step 4: Create the tweet with the uploaded media and user prompt
    tweet_response = oauth.post(
        "https://api.twitter.com/2/tweets",
        json={"text": tweet_text, "media": {"media_ids": [media_id]}},
        headers={"Content-Type": "application/json"}
    )
    if tweet_response.status_code == 201:
        tweet_data = tweet_response.json()
        tweet_id = tweet_data["data"]["id"]
        save_tweet_id(tweet_id)
        return jsonify({"success": True, "tweet_id": tweet_id, "image_url": image_url}), 201

    return jsonify({"error": "Failed to post tweet", "details": tweet_response.json()}), 400


@app.route('/twitter-image/dashboard')
def dashboard():
    leaderboard = []
    if os.path.exists(TWEETS_FILE):
        with open(TWEETS_FILE, 'r') as file:
            tweets_data = json.load(file)
            for tweet in tweets_data["tweets"]:
                tweet_info = get_tweet_data(tweet["id"])
                if tweet_info:
                    leaderboard.append(tweet_info)

    # Sort leaderboard by likes (descending)
    leaderboard.sort(key=lambda x: x["likes"], reverse=True)
    return jsonify({"items": leaderboard})


def get_tweet_data(tweet_id):
    api_url = f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&token=token"
    try:
        response = requests.get(api_url)
        response.raise_for_status()
        tweet_data = response.json()

        # Extract media URL from `entities.media` or `mediaDetails`
        media_url = (
            tweet_data.get("entities", {}).get("media", [{}])[0].get("media_url_https") or
            tweet_data.get("mediaDetails", [{}])[0].get("media_url_https", "")
        )
        # Check for required fields
        if all(field in tweet_data for field in ["id_str", "user"]):
            return {
                "id": tweet_data["id_str"],
                "author": tweet_data["user"]["name"],
                "prompt": tweet_data.get("text", ""), 
                "imageUrl": media_url, 
                "likes": tweet_data.get("favorite_count", 0),
                "socialMediaLink": f"https://twitter.com/{tweet_data['user']['screen_name']}/status/{tweet_id}"
            }
    except requests.exceptions.RequestException as e:
        print(f"Error fetching data for tweet ID {tweet_id}: {e}")
    return None


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


def validate_jwt(auth_header):
    if not auth_header or not auth_header.startswith("Bearer "):
        raise ValueError("Authorization token is missing or invalid")
    try:
        # Extract and decode the JWT
        jwt_token = auth_header.split(" ")[1]
        decoded_token = jwt.decode(jwt_token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        
        # Extract OAuth tokens from the decoded JWT
        oauth_token = decoded_token.get("oauth_token")
        oauth_token_secret = decoded_token.get("oauth_token_secret")
        if not oauth_token or not oauth_token_secret:
            raise ValueError("Invalid token")
        
        return oauth_token, oauth_token_secret
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError) as e:
        raise ValueError(f"Invalid or expired token: {str(e)}")


def download_image(image_url):
    # Send a GET request to download the image
    response = requests.get(image_url)
    if response.status_code != 200:
        raise ValueError("Failed to download image")
    return BytesIO(response.content)

if __name__ == '__main__':
    app.run(debug=True, port=5000)
