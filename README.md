# Python AlphaHub Connection

https://github.com/timolson/alphahub

This example code demonstrates usage of the [alphahub.us](https://alphahub.us) API. It's written in Python and requires 
the `websockets` and `requests` packages. It uses heartbeating to keep the connection alive and will automatically
reauthenticate and reconnect on any error.

## Running

1. Python 3.7 or greater is required
2. `pip install -r requirements.txt`
3. Provide your AlphaHub credentials by either:
   1. Set environment variables `ALPHAHUB_EMAIL` and/or `ALPHAHUB_PASSWORD` 
   2. if an environment variable is not found, then the
      package `alphahub.creds` is imported and the attributes `email` and
      `password` are used instead. See `src/alphahub/creds_example.py`
4. `set PYTHONPATH=src`
5. `python -m alphahub [algo_id]*`
   1. if no algorithm ID's are given on the command-line, it defaults to using 14, 16, 17, and 18.

## Connection Class Usage

You can easily integrate the `alphahub.Connection` class into your own program:

```python
import asyncio
import alphahub

def on_signal(id:int,info:dict):
    """
    :param id is integer ID of the algorithm sending the signal
    :param info is a dictionary like this one taken from Minotaur-3:
       {
          "close":[
              {"price":216.08,"side":"buy","symbol":"VRTX","timestamp":"2021-04-26 T14:30:00"},
              {"price":87.1,"side":"sell","symbol":"MU","timestamp":"2021-04-26T14:30:00"},
              {"price":80.28,"side":"sell"," symbol":"BMRN","timestamp":"2021-04-26T14:30:00"}
          ],
          "open":[
              {"price":168.56,"side":"buy","symbol":"JBHT","timestamp":"2021-04-26T14:30:01"},
              {"price":414.6,"side":"buy","symbol":"ILMN","timestamp":"2021-04-26T14:30:01"},
              {"price":142.94,"side":"sell","symbol":"CDNS","timestamp":"2021-04-26T14:30:01"}
          ]
      }
    """
    print('SIGNAL',id,info)


email = 'my@email.com'
password = 'abc123'
algo_ids = [14,16,17,18]
conn = alphahub.Connection(email,password,algo_ids,on_signal)
asyncio.run(conn.start())
```

The `Connection` class is a simple finite state machine and on any error it goes back to the initial state, causing a
re-authentication and new websockets connection to occur.

NOTE that this is 99% reliable, but if the connection drops just before a signal is published, and the connection is not
reestablished until after the signal was pushed, then the signal message will be missed. High-availability would require
multiple connections from different locations, and/or a change to the API to request missed signals.

## AlphaHub API Details

### Signal ID's

Each signal has an integer ID assigned by AlphaHub:
* `14` Minotaur 1-Stock
* `16` Minotaur 3-Stock
* `17` Minotaur Optimus 1
* `18` The Optimizer

If your signal isn't listed here, just browse to the signal page on alphahub.us and look at the URL: 
`https://alphahub.us/algorithms/18` would be signal ID `18`

### Authentication

The API authenticates with a REST-based OAuth request:

```python
  url = 'https://alphahub.us/api/v1/session'
  headers = {'Content-Type': 'application/x-www-form-urlencoded'}
  params = {'user[email]': email, 'user[password]': password}
  response = requests.post(url, params, headers=headers)
```

Response:

```json
{'data': {'renew_token': 'ABC...', 'token': 'ABC...'}}
```

### WebSockets Connection

The authentication token expires after 30 minutes but within that window may be used to create a WebSockets connection:

```python
url = f'wss://alphahub.us/socket/websocket?vsn=2.0.0&api_token={token}'
ws = await websockets.connect(url)
```

The rest of the communication is over the websocket.

### Signal Subscriptions

After connecting the websocket, you may make multiple signal subscription requests:

```python
ws.send(json.dumps([None,None,f'algorithms:{id}','phx_join',{}]))
```

Response:

```json
[null,null,"algorithms:14","phx_reply",{"response":{},"status":"ok"}]
```

### Heartbeat

The websocket connection will timeout and not produce signals unless you keep it active with an application-level
heartbeat. NOTE: this app-level heartbeat is in addition to the usual websockets protocol-level ping/pong
mechanism. The application timeout is about one minute, so we send a heartbeat about every 15 seconds:

Heartbeat request:

```json
[null,null,"phoenix","heartbeat",{}]
```

Heartbeat response:

```json
[null,null,"phoenix","phx_reply",{"response":{},"status":"ok"}]
```

### Signals

If the connection is up and you are heartbeating properly, then when new signals are published, you should receive a 
signal message for each signal you are subscribed to.

Signal message (pushed):

```json
[
   null,
   null,
   "algorithms:16",
   "new_signals",
   {
      "close":[
         {"price":216.08,"side":"buy","symbol":"VRTX","timestamp":"2021-04-26 T14:30:00"},
         {"price":87.1,"side":"sell","symbol":"MU","timestamp":"2021-04-26T14:30:00"},
         {"price":80.28,"side":"sell"," symbol":"BMRN","timestamp":"2021-04-26T14:30:00"}
      ],
      "open":[
         {"price":168.56,"side":"buy","symbol":"JBHT","timestamp":"2021-04-26T14:30:01"},
         {"price":414.6,"side":"buy","symbol":"ILMN","timestamp":"2021-04-26T14:30:01"},
         {"price":142.94,"side":"sell","symbol":"CDNS","timestamp":"2021-04-26T14:30:01"}
      ]
  }
]
```

The `info` argument passed to the `alphahub.Connection.on_message(id,info)` method is the last item of this message 
list.