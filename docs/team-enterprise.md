# Team and enterprise evidence

The local `team` configuration describes evidence ownership:

```yaml
team:
  name: platform-verification
  organization: example
  evidence_server: https://evidence.example.internal
  require_signed_bundles: true
```

Signed bundles may be stored in existing object storage, artifact systems, or a
future dedicated evidence server. VERITY remains local-first; an evidence server
is optional and must not receive secrets or unredacted agent transcripts.
