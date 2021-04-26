import asyncio
import logging
import sys

from ._connection import Connection
import os

log = logging.getLogger('alphahub.main')

def get_credentials():
    """
    The environment variables ALPHAHUB_EMAIL and ALPHAHUB_PASSWORD are checked first, and if either is missing, then
    the creds.py file is attempted to be imported and its email and password attributes are used.
    """
    email = os.environ.get('ALPHAHUB_EMAIL',None)
    password = os.environ.get('ALPHAHUB_PASSWORD',None)
    if not email or not password:
        try:
            from . import creds
            if not email:
                try:
                    email = creds.email
                except AttributeError:
                    pass
            if not password:
                try:
                    password = creds.password
                except AttributeError:
                    pass
        except ImportError:
            pass
    return email,password

def fatal(msg):
    log.critical(msg)
    exit(1)

async def main():
    logging.basicConfig(level=logging.DEBUG,format='%(asctime)-15s %(levelname)s %(name)s %(message)s')
    algo_ids = map(int,sys.argv[1:]) if sys.argv[1:] else [14, 16, 17]
    log.info(f'Starting for algos {algo_ids}')
    email,password = get_credentials()
    log.info(f'Found credentials for {email}')
    if email is None:
        fatal('Could not get email from ALPHAHUB_EMAIL environment variable or creds.py `email` variable')
    if password is None:
        fatal('Could not get password from ALPHAHUB_PASSWORD environment variable or creds.py `password` variable')
    connection = Connection(email, password, algo_ids)
    await connection.start()
    log.info('done')


asyncio.run(main())