# Acquire a pinned public Hugging Face model

Handle acquisition independently on each authorized runner. First check the
approved local caches and prefer an already verified snapshot. If a download
is needed, use a private case directory and the exact `repo_id` and full Git
revision from the request. Reuse verified files and preserve resumable partials
for that same revision; never substitute a moving branch or another model.

1. Try the official `https://huggingface.co` endpoint. For a public, ungated
   model, if the runner hits a DNS/connectivity error, timeout, or temporary
   gateway failure, try an allowed alternate source: `https://hf-mirror.com`
   or [ModelScope](https://modelscope.cn/). Use the source allowed and reachable
   on that runner. This is an in-scope fallback when public download and
   access to that source are already authorized; no second confirmation is
   needed. An official-only restriction, authentication/access denial, or gated
   model is not a reason to try another source. Do not change shared proxy, DNS
   or tool configuration, disable TLS verification, or send tokens to the
   fallback sources.
2. Use a per-command endpoint in a private case cache. Example with the
   existing authorized `hf` client (fill in trusted values, no credentials):

   ```sh
   env -u HF_TOKEN -u HUGGING_FACE_HUB_TOKEN -u HF_TOKEN_PATH \
     HF_HUB_DISABLE_IMPLICIT_TOKEN=1 HF_HOME="$CASE_DIR/hf-cache" \
     HF_ENDPOINT=https://hf-mirror.com \
     hf download "$MODEL_REPO" --revision "$MODEL_REVISION" --local-dir "$MODEL_DIR"
   ```

3. For ModelScope, use a known publisher mapping and an explicit ModelScope
   repository and snapshot revision. The repository name and revision namespace
   can differ from Hugging Face: do not pass the Hugging Face commit as a
   ModelScope revision unless that exact mapping is known. Never pick the latest
   release or a variant just because the model name matches. Acquire only the
   required files into the private case directory, with no authentication.
4. Before loading anything from either fallback, verify the complete required
   file inventory against the trusted **official** pinned manifest: Git LFS
   SHA-256 and size for weights; official Git blob identity or trusted content
   hashes for required config, tokenizer and index files. A ModelScope snapshot
   is eligible only when those files match the requested Hugging Face revision
   byte-for-byte. An already verified official manifest can be reused. A
   fallback's own metadata is not an independent checksum source. If official
   metadata is unobtainable through an approved channel, verification fails, or
   all allowed sources are unavailable, stop that side, retain verified work,
   record the specific blocker and continue any independent work. Record the
   actual source, its revision, the requested revision and verified file
   coverage in the environment receipt. Do not load unverified files or
   auto-enable remote code from a fallback source.
