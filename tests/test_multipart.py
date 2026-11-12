"""The upload body for /v1/audio/transcriptions, read back the way a server reads it."""
from email.parser import BytesParser
from email.policy import HTTP

from localdub.llm import multipart


def parse(body: bytes, content_type: str):
    msg = BytesParser(policy=HTTP).parsebytes(f"Content-Type: {content_type}\r\n\r\n".encode() + body)
    assert msg.is_multipart(), "the Content-Type must say multipart/form-data with the boundary"
    return list(msg.iter_parts())


def test_fields_and_file_come_through():
    audio = b"RIFF\x00\x01binary\r\n--not-a-boundary\xff"  # bytes that aren't text, and even look like markup
    body, ctype = multipart([("model", "nemotron"), ("language", "bg")], "file", "part00.wav", audio)
    parts = parse(body, ctype)
    assert len(parts) == 3, "two text fields and the file"
    model, language, file = parts
    assert model.get_param("name", header="content-disposition") == "model"
    assert model.get_payload(decode=True) == b"nemotron" and language.get_payload(decode=True) == b"bg"
    assert file.get_param("name", header="content-disposition") == "file"
    assert file.get_filename() == "part00.wav", "the file part needs filename=\"...\""
    assert file.get_content_type() == "audio/wav", "the file part needs its own Content-Type line"
    assert file.get_payload(decode=True) == audio, "the file's bytes must arrive unchanged"


def test_body_ends_with_the_closing_boundary():
    body, ctype = multipart([("model", "nemotron")], "file", "a.wav", b"x")
    boundary = ctype.split("boundary=")[1]
    assert body.endswith(f"--{boundary}--\r\n".encode())
