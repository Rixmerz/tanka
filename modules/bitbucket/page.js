// Bitbucket on the page (docs/page.md): what the chat says while a Bitbucket tool runs, and a line in the greeting
// when the module is not configured. Approve and Dismiss for pipeline requests come from the approvals module.
(() => {
  const T = {
    notConfigured: "Bitbucket is installed but not configured yet: run tanka bitbucket status in a terminal.",
    using: name => {
      if (/bitbucket_run_pipeline/.test(name)) return "asking Bitbucket to run a pipeline…";
      if (/bitbucket_stop_pipeline/.test(name)) return "asking Bitbucket to stop a pipeline…";
      return /^bitbucket_/.test(name) ? "reading Bitbucket…" : null;
    },
  };

  Tanka.module("bitbucket", {
    using: T.using,
    hello: (sc, mod) => (mod && mod.configured === false ? T.notConfigured : ""),
  });
})();
