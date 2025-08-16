import os
import time
import json
import logging
from datetime import datetime, timedelta
import webbrowser

import requests
from requests_oauthlib import OAuth2Session
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# --- Configuration & Setup ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Load environment variables from .env file
# Create a .env file based on .env.example if it doesn't exist
if not os.path.exists('.env'):
    logging.warning(".env file not found. Please create it based on .env.example and fill in your credentials.")
    # Consider exiting here in a real application, depending on requirements
    # exit()
load_dotenv()

CLIENT_ID = os.getenv('LINKEDIN_CLIENT_ID')
CLIENT_SECRET = os.getenv('LINKEDIN_CLIENT_SECRET')
# This is the URL your server will listen on locally for the callback
REDIRECT_URI = 'http://localhost:8080/callback'
# We'll store the token and user info here temporarily.
# In a real app, use a more persistent method (file, database).
token_info = {"access_token": os.getenv('LINKEDIN_ACCESS_TOKEN')}
user_urn = os.getenv('LINKEDIN_USER_URN') # e.g., 'urn:li:person:xxxx' or 'urn:li:organization:yyyy'

# LinkedIn API endpoints
AUTHORIZATION_URL = 'https://www.linkedin.com/oauth/v2/authorization'
ACCESS_TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
API_BASE_URL = 'https://api.linkedin.com/v2/'

# Define required OAuth scopes
# 'r_liteprofile' and 'r_emailaddress' are needed for basic profile info (like getting the URN)
# 'w_member_social' is needed for posting shares on behalf of the member
SCOPES = ['r_liteprofile', 'w_member_social'] # Add 'w_organization_social' if posting to a page

# --- LinkedIn API Interaction ---

def get_oauth_session(token=None):
    """Creates an OAuth2Session object."""
    return OAuth2Session(CLIENT_ID, redirect_uri=REDIRECT_URI, scope=SCOPES, token=token)

def perform_oauth_flow():
    """Guides the user through the LinkedIn OAuth2 authentication process."""
    global token_info, user_urn # Allow modification of global vars

    if not CLIENT_ID or not CLIENT_SECRET:
        logging.error("Client ID or Client Secret not found in .env file. Cannot authenticate.")
        return False

    linkedin = get_oauth_session()
    authorization_url, state = linkedin.authorization_url(AUTHORIZATION_URL)

    print(f"Please go to this URL and authorize the application:\n{authorization_url}")
    print("\nOpening browser...")
    try:
        webbrowser.open(authorization_url)
    except Exception as e:
        logging.warning(f"Could not open browser automatically: {e}. Please copy the URL manually.")


    # --- !!! IMPORTANT MANUAL STEP !!! ---
    # The user needs to authorize in the browser, and LinkedIn will redirect
    # them to REDIRECT_URI (e.g., http://localhost:8080/callback?code=AUTH_CODE&state=STATE).
    # You MUST capture the full redirect URL from their browser address bar after authorization.
    # For this basic script, we'll ask the user to paste it.
    # A real web application would run a temporary web server to catch this.
    # --- !!! /IMPORTANT MANUAL STEP !!! ---

    redirect_response = input("Paste the full redirect URL from your browser after authorizing: ")

    if not redirect_response:
        logging.error("No redirect URL provided. Authentication failed.")
        return False

    try:
        # Fetch the access token
        token_info = linkedin.fetch_token(
            ACCESS_TOKEN_URL,
            client_secret=CLIENT_SECRET,
            authorization_response=redirect_response # Pass the full URL
        )
        logging.info("Successfully obtained access token.")

        # Fetch the user's URN (needed for posting)
        profile_info = get_profile_info(linkedin)
        if profile_info and 'id' in profile_info:
            user_urn = f"urn:li:person:{profile_info['id']}"
            logging.info(f"Obtained user URN: {user_urn}")
            # Persist the token and URN for future use (e.g., save to .env or a config file)
            # This part is manual for now - you'd ideally script updating .env
            print("\n--- Authentication Successful ---")
            print(f"Your Access Token: {token_info.get('access_token')}")
            print(f"Your User URN: {user_urn}")
            print("RECOMMENDATION: Update your .env file with these values to avoid authenticating next time.")
            print("--------------------------------\n")
            return True
        else:
            logging.error("Could not retrieve user URN after authentication.")
            return False

    except Exception as e:
        logging.error(f"Error during token fetch or profile info retrieval: {e}")
        return False


def get_profile_info(oauth_session):
    """Fetches basic profile information, including the user ID."""
    try:
        profile_url = f"{API_BASE_URL}me" # Basic profile endpoint
        response = oauth_session.get(profile_url)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Error fetching profile info: {e}")
        if hasattr(e, 'response') and e.response is not None:
            logging.error(f"Response Content: {e.response.text}")
        return None

def post_linkedin_update(text_content):
    """Posts a text update to the authenticated user's LinkedIn profile."""
    if not token_info or 'access_token' not in token_info:
        logging.error("No valid access token found. Please authenticate first.")
        # Attempt re-authentication maybe? Or just fail.
        # authenticated = perform_oauth_flow()
        # if not authenticated: return False # Added return False
        return False # Keep it simple for now

    if not user_urn:
        logging.error("User URN not set. Cannot determine where to post.")
        # Could try to fetch it again if we have a token
        return False

    linkedin = get_oauth_session(token=token_info)

    post_api_url = f"{API_BASE_URL}ugcPosts" # API endpoint for User Generated Content Posts

    # Construct the request body according to LinkedIn API v2 spec
    # More complex posts (images, URLs) require different structures.
    post_body = {
        "author": user_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": text_content
                },
                "shareMediaCategory": "NONE" # Use "ARTICLE" for links, "IMAGE" for images etc.
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "CONNECTIONS" # Or "PUBLIC", "LOGGED_IN"
        }
    }

    try:
        headers = {'Content-Type': 'application/json', 'X-Restli-Protocol-Version': '2.0.0'}
        response = linkedin.post(post_api_url, headers=headers, json=post_body)
        response.raise_for_status() # Check for HTTP errors
        logging.info(f"Successfully posted update to LinkedIn: '{text_content[:50]}...'")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Error posting to LinkedIn: {e}")
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_details = e.response.json()
                logging.error(f"API Error Details: {json.dumps(error_details, indent=2)}")
            except json.JSONDecodeError:
                logging.error(f"Response Content: {e.response.text}")
        # Here you might check if the error is related to an expired token
        # and potentially trigger a refresh or re-authentication flow.
        return False
    except Exception as e:
        # Catch any other unexpected errors during posting
        logging.error(f"An unexpected error occurred during posting: {e}")
        return False


# --- Scheduling Logic ---

scheduler = BackgroundScheduler(timezone="UTC") # Use UTC for consistency

def schedule_linkedin_post(post_time, text_content):
    """Schedules a LinkedIn post for a specific time."""
    if not isinstance(post_time, datetime):
        logging.error("Invalid post_time provided. It must be a datetime object.")
        return

    # A bit of human touch: check if the time is in the past.
    # While APScheduler handles this, an explicit check is user-friendly.
    if post_time < datetime.now(post_time.tzinfo or None):
         # Check against aware or naive datetime depending on input
        logging.warning(f"Scheduled time {post_time} is in the past. Posting immediately (or skipping if scheduler already started).")
        # Depending on desired behavior, you could post immediately or just log and skip
        # For now, let APScheduler handle it (it might run immediately if due).

    # Add job to the scheduler
    # Using a unique ID based on time and content hash could prevent duplicates if needed
    job_id = f"linkedin_post_{int(post_time.timestamp())}_{hash(text_content)}"
    scheduler.add_job(
        post_linkedin_update,
        trigger='date',
        run_date=post_time,
        args=[text_content],
        id=job_id,
        name=f"LinkedIn: {text_content[:30]}...",
        replace_existing=True # Replace if a job with the same ID exists
    )
    logging.info(f"Scheduled post for {post_time}: '{text_content[:50]}...'")

def list_scheduled_posts():
    """Prints a list of currently scheduled posts."""
    jobs = scheduler.get_jobs()
    if not jobs:
        print("No posts currently scheduled.")
        return

    print("--- Scheduled Posts ---")
    for job in jobs:
        # APScheduler job attributes might vary slightly based on version
        run_time_local = job.next_run_time.astimezone() if job.next_run_time else "N/A"
        print(f"ID: {job.id}")
        print(f"  Run Time (Local): {run_time_local}")
        print(f"  Content: {job.kwargs.get('text_content', 'N/A')}") # Accessing arg via kwargs
        print("-" * 20)
    print("-----------------------\n")


# --- Main Execution / Example Usage ---

if __name__ == "__main__":
    # Check if we have a token and URN, otherwise trigger OAuth flow
    if not token_info.get('access_token') or not user_urn:
        logging.info("Access token or user URN not found in environment. Starting authentication flow.")
        if not perform_oauth_flow():
            logging.critical("Authentication failed. Exiting.")
            exit()
        else:
            logging.info("Authentication successful. You might want to restart the script after updating .env.")
            # Note: The script continues here, but a restart ensures the loaded .env values are fresh.
    else:
        logging.info("Access token and user URN loaded from environment.")
        # Optional: Add a check here to verify the token is still valid with a simple API call

    # Start the scheduler in the background
    scheduler.start()
    logging.info("Scheduler started. Ready to schedule posts. Press Ctrl+C to exit.")

    # --- Example Usage ---
    # Schedule a post for 1 minute from now
    schedule_time_1 = datetime.now() + timedelta(minutes=1)
    schedule_linkedin_post(schedule_time_1, f"Hello from my Python scheduler! This post was scheduled at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.")

    # Schedule another post for 5 minutes from now
    schedule_time_2 = datetime.now() + timedelta(minutes=5)
    schedule_linkedin_post(schedule_time_2, "This is a second scheduled post to LinkedIn via Python and APScheduler. #Python #Automation #LinkedInAPI")

    # Keep the script running so the scheduler can execute jobs
    try:
        while True:
            print("---")
            list_scheduled_posts()
            print("Scheduler running... Check logs for posting activity. (Press Ctrl+C to stop)")
            # A less busy loop than immediate pass, sleep for a while.
            # This makes it feel less like a tight, purely functional loop.
            time.sleep(60) # Check scheduled posts every minute

    except (KeyboardInterrupt, SystemExit):
        # Nicely shut down the scheduler on exit
        logging.info("Shutting down scheduler...")
        scheduler.shutdown()
        logging.info("Scheduler stopped.") 