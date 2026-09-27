import json
from pathlib import Path
import sys
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from theatre_bot.channels import OutgoingButton, OutgoingMessage
from theatre_bot.dialog import Reply
from theatre_bot.outgoing_delivery import MemoryHttpTransport, MaxApiSender, VkApiSender


vk_transport = MemoryHttpTransport()
max_transport = MemoryHttpTransport()
message_buttons = (OutgoingButton("subscribe", "Подписаться"),)

VkApiSender(
    "demo-vk-token", vk_transport, random_id=lambda: 100
).send(OutgoingMessage("vk", "17", Reply("Будем рады видеть Вас в театре!"), message_buttons))
MaxApiSender("demo-max-token", max_transport).send(
    OutgoingMessage("max", "18", Reply("Будем рады видеть Вас в театре!"), message_buttons)
)

vk_request = vk_transport.requests[0]
max_request = max_transport.requests[0]
vk_fields = parse_qs(vk_request.body.decode("utf-8"))
max_body = json.loads(max_request.body)

print(f"VK: {vk_request.method} {vk_request.url}; получатель: {vk_fields['peer_id'][0]}")
print(f"MAX: {max_request.method} {max_request.url.split('?', 1)[0]}; кнопок: {len(max_body['attachments'][0]['payload']['buttons'])}")
print("Токены в отчёт не выводились. MemoryHttpTransport не обращался во внешнюю сеть.")
