from app.component.settings import \
    log, \
    EVENT_STORE_URL, \
    EVENT_STORE_HTTP_PORT, \
    EVENT_STORE_TCP_PORT, \
    EVENT_STORE_USER, \
    EVENT_STORE_PASS
import asyncio
from photonpump import connect, exceptions
import json
import functools
import uuid
import requests
import pyaml


def run_in_executor(f):
    """
    wraps a blocking (non-asyncio) function to execute it in the loop as if it were an async func
    """
    @functools.wraps(f)
    def inner(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, functools.partial(f, *args, **kwargs))
    return inner


def build_text(response_json):
    del response_json['extension']
    del response_json['meta']
    return pyaml.dump(response_json)


@run_in_executor
def create_response(event):
    event_data = json.loads(event.data)
    response = requests.get(
        "https://directory.spineservices.nhs.uk/STU3/Organization/%s" % event_data['args'][0]
    )
    if response.status_code == 200:
        return build_text(response.json())
    else:
        return "Sorry, I couldn't find an organisation for %s" % event_data['args'][0]


@run_in_executor
def post_to_dialogue_stream(event, result_text):
    event_data = json.loads(event.data)
    headers = {
        "ES-EventType": "response_created",
        "ES-EventId": str(uuid.uuid1())
    }
    requests.post(
        "http://%s:%s/streams/dialogue" % (EVENT_STORE_URL, EVENT_STORE_HTTP_PORT),
        headers=headers,
        json={"event_id": event_data["event_id"], "response": result_text}
    )


def meets_criteria(event)->bool:
    """
    :param data object from event: 
    :return: bool based on whether this component should process the event object
    """
    return event.type == "text_parsed" and json.loads(event.data)["command"].lower() == "ods"


async def create_subscription(subscription_name, stream_name, conn):
    await conn.create_subscription(subscription_name, stream_name)


async def aggregate_fn():
    _loop = asyncio.get_event_loop()
    async with connect(
            host=EVENT_STORE_URL,
            port=EVENT_STORE_TCP_PORT,
            username=EVENT_STORE_USER,
            password=EVENT_STORE_PASS,
            loop=_loop
    ) as c:
        await c.connect()
        try:
            await create_subscription("spine", "dialogue", c)
        except exceptions.SubscriptionCreationFailed as e:
            if e.message.find("'spine' already exists."):
                log.info("spine dialogue subscription found.")
            else:
                log.exception(e)
        dialogue_stream = await c.connect_subscription("spine", "dialogue")
        async for event in dialogue_stream.events:
            if meets_criteria(event):
                log.debug("aggregate_fn() responding to: %s" % json.loads(event.data))
                try:
                    await post_to_dialogue_stream(event, await create_response(event))
                    await dialogue_stream.ack(event)
                except Exception as e:
                    log.exception(e)
            else:
                await dialogue_stream.ack(event)


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop())
    mainloop = asyncio.get_event_loop()
    mainloop.run_until_complete(aggregate_fn())
