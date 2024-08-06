from http import HTTPStatus

import httpx
import msgspec

# req = {
#     "query": [
#         "what a nice day",
#         "life is short, use python",
#         "early bird catches the worm",
#     ],
# }
req = {
    "query": [
        "what a nice day",
    ],
}

resp = httpx.post(
    "http://127.0.0.1:8000/inference", content=msgspec.msgpack.encode(req)
)
if resp.status_code == HTTPStatus.OK:
    print(f"OK: {msgspec.msgpack.decode(resp.content)}")
else:
    print(f"err[{resp.status_code}] {resp.text}")
