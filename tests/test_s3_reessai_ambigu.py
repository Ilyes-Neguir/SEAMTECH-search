"""Sonde de réessai SDK et réponse perdue ; ne contacte que loopback."""
from __future__ import annotations

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from seamtech_search.storage import S3StorageClient, StorageError


@pytest.mark.parametrize("mode", ["octets", "fichier"])
def test_reponse_perdue_ne_doit_pas_ecraser_un_ecrivain_concurrent(monkeypatch, tmp_path, mode):
    etat = {"appels": 0, "contenu": b""}

    class Reception(BaseHTTPRequestHandler):
        def do_PUT(self):  # noqa: N802
            corps = self.rfile.read(int(self.headers["Content-Length"]))
            etat["appels"] += 1
            etat["contenu"] = corps
            if etat["appels"] == 1:
                # Le premier PUT a abouti, puis un concurrent a écrit avant la
                # perte de réponse. Le serveur ferme le flux sans réponse HTTP.
                etat["contenu"] = b"autre-ecrivain"
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Reception)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        client = S3StorageClient(endpoint_url=f"http://127.0.0.1:{serveur.server_port}",
                                 bucket_name="banc-synthetique", access_key_id="identite-synthetique",
                                 secret_access_key="secret-synthetique")
        monkeypatch.setattr(client, "ensure_bucket_exists", lambda: None)
        with pytest.raises(StorageError):
            if mode == "octets":
                client.upload_bytes(b"premier-envoi", "cle-synthetique", avoid_overwrite=False)
            else:
                source = tmp_path / "piece"
                source.write_bytes(b"premier-envoi")
                client.upload_file(source, "cle-synthetique", avoid_overwrite=False)
        assert etat["appels"] == 1
        assert etat["contenu"] == b"autre-ecrivain"
    finally:
        serveur.shutdown()
        serveur.server_close()
        thread.join()


def test_reponse_upload_part_perdue_sans_reessai_sdk(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit

    etat = {"partie_1": 0}

    class Reception(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            assert "uploads" in self.path
            corps = b"<InitiateMultipartUploadResult><UploadId>synthetique</UploadId></InitiateMultipartUploadResult>"
            self.send_response(200)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def do_PUT(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            numero = parse_qs(urlsplit(self.path).query)["partNumber"][0]
            if numero == "1":
                etat["partie_1"] += 1
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.send_response(200)
            self.send_header("ETag", '"synthetique"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_DELETE(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Reception)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        client = S3StorageClient(endpoint_url=f"http://127.0.0.1:{serveur.server_port}",
                                 bucket_name="banc-synthetique", access_key_id="identite-synthetique",
                                 secret_access_key="secret-synthetique")
        monkeypatch.setattr(client, "ensure_bucket_exists", lambda: None)
        source = tmp_path / "piece"
        source.write_bytes(b"x" * (9 * 1024 * 1024))
        with pytest.raises(StorageError):
            client.upload_file(source, "cle-synthetique", avoid_overwrite=False)
        assert etat["partie_1"] == 1
    finally:
        serveur.shutdown()
        serveur.server_close()
        thread.join()


def test_reponse_completion_multipart_perdue_sans_reessai_sdk(monkeypatch, tmp_path):
    from urllib.parse import parse_qs, urlsplit

    etat = {"completion": 0, "parties": set()}

    class Reception(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
            if "uploads" in query:
                corps = (b"<InitiateMultipartUploadResult>"
                         b"<UploadId>synthetique</UploadId></InitiateMultipartUploadResult>")
                self.send_response(200)
                self.send_header("Content-Length", str(len(corps)))
                self.end_headers()
                self.wfile.write(corps)
            elif "uploadId" in query:
                self.rfile.read(int(self.headers["Content-Length"]))
                etat["completion"] += 1
                # Le fournisseur a validé l'objet, mais sa réponse disparaît.
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
            else:
                self.send_error(400)

        def do_PUT(self):  # noqa: N802
            self.rfile.read(int(self.headers["Content-Length"]))
            numero = parse_qs(urlsplit(self.path).query)["partNumber"][0]
            etat["parties"].add(numero)
            self.send_response(200)
            self.send_header("ETag", f'"partie-{numero}"')
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_DELETE(self):  # noqa: N802
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *_args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Reception)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        client = S3StorageClient(endpoint_url=f"http://127.0.0.1:{serveur.server_port}",
                                 bucket_name="banc-synthetique", access_key_id="identite-synthetique",
                                 secret_access_key="secret-synthetique")
        monkeypatch.setattr(client, "ensure_bucket_exists", lambda: None)
        source = tmp_path / "piece"
        source.write_bytes(b"x" * (9 * 1024 * 1024))
        with pytest.raises(StorageError):
            client.upload_file(source, "cle-synthetique", avoid_overwrite=False)
        assert etat["parties"] == {"1", "2"}
        assert etat["completion"] == 1
    finally:
        serveur.shutdown()
        serveur.server_close()
        thread.join()


def test_reponse_suppression_perdue_etat_distant_ambigu():
    etat = {"appels": 0, "present": True}

    class Reception(BaseHTTPRequestHandler):
        def do_DELETE(self):  # noqa: N802
            etat["appels"] += 1
            etat["present"] = False
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()

        def log_message(self, *_args):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Reception)
    thread = threading.Thread(target=serveur.serve_forever, daemon=True)
    thread.start()
    try:
        client = S3StorageClient(endpoint_url=f"http://127.0.0.1:{serveur.server_port}",
                                 bucket_name="banc-synthetique", access_key_id="identite-synthetique",
                                 secret_access_key="secret-synthetique")
        assert client.delete_file("cle-synthetique") is False
        assert etat == {"appels": 1, "present": False}
    finally:
        serveur.shutdown()
        serveur.server_close()
        thread.join()
