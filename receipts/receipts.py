from __future__ import print_function
import pickle
import os.path
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# If modifying these scopes, delete the file token.pickle.
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/spreadsheets',
    ]


class receipts:
    def _init_(self):
        True


def main():
    """Shows basic usage of the Drive v3 API.
    Prints the names and ids of the first 10 files the user has access to.
    """
    creds = None
    # The file token.pickle stores the user's access and refresh tokens, and is
    # created automatically when the authorization flow completes for the first
    # time.
    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)
    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                '/home/sauer/Downloads/credentials.json', SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('drive', 'v3', credentials=creds)

    # Get (after first creating as needed) the containing folder
    target = 'Receipts'
    file = None
    result = service.files().list(
        q=f""""
            mimeType     = 'application/vnd.google-apps.folder'
            and name     = '{target}'
            and trashed != true
        """,
        spaces='drive',
        fields='files(id, name)',
        ).get('files', [])
    if result:
        print("Found target")
        if len(result) > 1:
            print("some kind of warning with logger")
            # and then look in to adding metadata which distinguishes ours
        file = result[0]
    else:
        print(f"Creating new directory {target}\n")
        file_metadata = {
            'name': 'Receipts',
            'mimeType': 'application/vnd.google-apps.folder'
        }
        file = service.files(
            ).create(body=file_metadata, fields='id').execute()
    target_id = file.get('id')
    print(f"Folder (name: {file.get('name')}) ID: {target_id}")


if __name__ == '__main__':
    main()
