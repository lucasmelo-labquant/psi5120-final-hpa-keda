import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ManifestTest(unittest.TestCase):
    def test_kubernetes_manifests_are_valid_yaml_documents(self):
        paths = [
            ROOT / "manifests/00-namespace.yaml",
            ROOT / "manifests/10-hpa.yaml",
            ROOT / "manifests/20-keda.yaml",
        ]
        for path in paths:
            with self.subTest(path=path.name):
                documents = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
                self.assertTrue(documents)
                self.assertTrue(
                    all(
                        "apiVersion" in document and "kind" in document
                        for document in documents
                    )
                )

    def test_rendered_worker_template_is_valid_yaml(self):
        content = (ROOT / "manifests/01-worker.yaml.template").read_text(
            encoding="utf-8"
        )
        content = content.replace("REPLACE_IMAGE_URI", "example.invalid/worker:test")
        content = content.replace("REPLACE_SOURCE_HASH", "0" * 64)
        content = content.replace(
            "REPLACE_INPUT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/1/input"
        )
        content = content.replace(
            "REPLACE_RESULT_QUEUE_URL", "https://sqs.us-east-1.amazonaws.com/1/result"
        )
        document = yaml.safe_load(content)
        self.assertEqual(document["kind"], "Deployment")
        self.assertEqual(document["spec"]["replicas"], 1)


if __name__ == "__main__":
    unittest.main()
