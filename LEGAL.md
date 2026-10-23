# Legal notice

Read this before using, distributing, or depending on this project.

## 1. This is an independent, unofficial project

This project is **not affiliated with, endorsed by, sponsored by, or supported
by OPPO** or any of its subsidiaries or affiliates.

"OPPO" and "心力球" (Xinliqiu) are trademarks of their respective owners. They
are used here **only in a descriptive, nominative sense** to identify the
hardware this software interoperates with. No trademark licence is granted or
implied.

Nothing in this repository is an official OPPO product, documentation, or
statement of support.

## 2. Interoperability, not circumvention

This project exists to let people **access data they already own** on hardware
they already possess, using credentials they generated themselves.

The SDK specifically:

- does **not** bypass, disable, or remove any authentication;
- does **not** crack, derive, or extract any key;
- does **not** obtain credentials from any third party;
- requires the operator to supply their own device credentials and their own
  private key.

Audio on the device is encrypted with a **public key the owner themselves
installed on that device**. This SDK performs the corresponding private-key
operation on data belonging to that owner. It does not grant access to anyone
else's data.

**Stated plainly, since this is the point most likely to be tested:**

> This project provides no ability to decrypt another person's recordings, and
> no method for bypassing official pairing or authorisation. Operators must use
> the credentials of their own device and a private key they hold themselves.

There is no backdoor, no universal key, no shared secret, and no path in this
code that reaches data without the operator's own key material. The pairing and
authentication exchanges it performs are the same ones the device expects from
any authorised client; nothing is disabled, patched, or skipped.

## 3. What this project does not include

For the avoidance of doubt, this repository contains **no**:

- firmware images or firmware extracts;
- vendor binaries, decompiled vendor code, or symbol tables;
- reverse-engineering notes, disassembly, or analysis artifacts;
- device credentials, pairing material, or private keys;
- cloud API keys, account tokens, or service credentials of any party.

## 4. Your responsibilities as an operator

By using this software you confirm that:

1. you own, or are authorised to use, the device you connect to;
2. the credentials and private keys you supply are yours, and you accept the
   consequences of using them;
3. you will not use this software to access data belonging to another person;
4. you understand that you operate at your own risk, including possible
   warranty or service consequences from your hardware vendor;
5. you will comply with the laws of your jurisdiction and with any agreements
   that bind you.

If you do not own the device, or do not have the credentials, **do not use this
software**.

## 5. Firmware and modification

This project does not, and will not, provide:

- firmware flashing, writing, or modification;
- format, erase, or factory-reset operations;
- pairing or key-installation procedures;
- any operation that changes persistent device state.

These were deliberately excluded. Their absence is a design decision, not an
unfinished feature.

## 6. No warranty

This software is provided **"as is", without warranty of any kind**, express or
implied, including but not limited to the warranties of merchantability,
fitness for a particular purpose, and non-infringement.

In no event shall the authors or copyright holders be liable for any claim,
damages, or other liability, whether in an action of contract, tort, or
otherwise, arising from, out of, or in connection with the software or the use
or other dealings in the software.

This includes, without limitation: data loss, device malfunction, voided
warranty, service disruption, or any consequence of operating the device.

## 7. Privacy

This SDK contains no telemetry, no analytics, and no phone-home behaviour.

Data flows only where you direct it:

- device to your machine over Bluetooth;
- your machine to a storage backend **you** deploy and control, if you enable it.

No maintainer of this project receives or can access your audio, credentials, or
metadata.

## 8. Licence

Source code in this repository is released under the MIT Licence. See
[LICENSE](LICENSE).

The MIT Licence applies to the code here. It does not grant any rights in
third-party trademarks, protocols, or hardware.

## 9. Reporting concerns

If you are a rights holder and believe this project exceeds legitimate
interoperability, open an issue and describe the specific concern. Good-faith
reports are taken seriously.

---

**Summary:** this is an unofficial interoperability tool for data on hardware
you already own. It does not break encryption, does not include vendor
material, and does not ship credentials. Use it on your own device, with your
own keys, at your own risk.
