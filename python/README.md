# xinliqiu-sdk (Python)

See the [project README](../README.md).

```bash
pip install "xinliqiu-sdk[crypto]"
```

```python
from aipin_sdk import Xinliqiu, Credentials

ball = Xinliqiu(Credentials(device_id="...", peer_id_file="...", key_file="..."))
print(ball.capabilities())
```
