#!/usr/bin/env python3
import pickle
import os.path
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

# If modifying these scopes, delete the file token.pickle.


class receipts:
    """ main receipt upload class
    """
    def __init__(self):
        self.SCOPES = [
            'https://www.googleapis.com/auth/drive.file',
            # 'https://www.googleapis.com/auth/spreadsheets',
        ]
        self.folder_id = None
        self.folder_name = 'Receipt images'
        self.folder_type = 'application/vnd.google-apps.folder'
        self.sheet_id = None
        self.sheet_name = 'Receipts sheet'
        self.sheet_type = 'application/vnd.google-apps.spreadsheet'
        self.service = None
        self._auth('/home/sauer/google_receipt-upload.json')
        self._init_folder()
        self._init_sheet()

    def _auth(self, cred_file):
        cache_file = 'token.pickle'
        creds = None
        # The cache_file stores the user's access and refresh tokens, and is
        # created automatically when the authorization flow completes for the
        # first time.
        if os.path.exists(cache_file):
            with open(cache_file, 'rb') as token:
                creds = pickle.load(token)

        # If there are no (valid) credentials available, let the user log in.
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    cred_file, self.SCOPES
                    )
                creds = flow.run_local_server(port=0)
            # Save the credentials for the next run
            with open(cache_file, 'wb') as token:
                pickle.dump(creds, token)

        self.service = build('drive', 'v3', credentials=creds)

    def get_or_create(self, name, mime_type):
        """ Get (after first creating as needed) the named item
        """
        file = None
        result = self.service.files().list(
            q=(f"mimeType     = '{mime_type}' "
               f"and name     = '{name}' "
               f"and trashed != true "
               ),
            spaces='drive',
            fields='files(id, name)',
        ).execute().get('files', [])
        if result:
            print("Found target")
            if len(result) > 1:
                print("some kind of warning with logger")
                # and then look in to adding metadata which distinguishes ours
            file = result[0]
        else:
            print(f"Creating new object {name}\n")
            file_metadata = {
                'name': name,
                'mimeType': mime_type
            }
            file = self.service.files(
                ).create(body=file_metadata, fields='id').execute()
        return file.get('id')

    def _init_folder(self):
        self.folder_id = self.get_or_create(
            self.folder_name,
            self.folder_type
            )
        print(f"Folder (name: {self.folder_name}) ID: {self.folder_id}")

    def _init_sheet(self):
        self.sheet_id = self.get_or_create(
            self.sheet_name,
            self.sheet_type
            )
        print(f"Sheet (name: {self.sheet_name}) ID: {self.sheet_id}")


def main():
    r = receipts()
    print(r)


if __name__ == '__main__':
    main()
