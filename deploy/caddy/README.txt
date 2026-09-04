Hosting several projects on one box
===================================

Caddy reads /etc/caddy/Caddyfile, which does nothing but set global options and
import one file per site:

    import /etc/caddy/sites.d/*.caddy

setup-oracle.sh writes ONLY /etc/caddy/sites.d/sarathi.caddy. That is the whole
point of the layout: re-running it, or redeploying sarathi, cannot disturb the
other projects sharing the machine. Each project owns one file, named after
itself, and nothing owns the main Caddyfile.

Adding a project
----------------

1. Point DNS at the box. One A record per hostname, all to the same IP:

     sarathi.example.org.  A  <your public IP>
     brahmo.example.org.   A  <your public IP>

   Caddy gets a certificate per hostname over HTTP, so no wildcard and no DNS
   plugin is needed. The names must resolve BEFORE you reload, or the ACME
   challenge fails and Caddy retries with a backoff.

2. Give the project its own port. Sarathi holds 8420; pick 8421, 8422, ...

3. Write /etc/caddy/sites.d/<project>.caddy — see site.caddy.example.

4. Validate, then reload. Reload keeps the old config if the new one is bad:

     sudo caddy validate --config /etc/caddy/Caddyfile
     sudo systemctl reload caddy

What this box can actually hold
-------------------------------

One OCPU. Measured on this hardware, sarathi's simulation takes 37 ms of every
50 ms tick on the default scene — most of the core, continuously, because the
service runs with --keep-warm.

So a second *busy* project will contend with it and both will feel slow. A
second *quiet* project — a static site, a small API, anything that is idle
between requests — costs almost nothing and is fine. Judge by what the project
does at rest, not by how many there are.

Once a second always-on service exists, consider dropping --keep-warm from
sarathi.service. It exists only to keep Oracle from reclaiming an instance it
thinks is idle, and Oracle's test is CPU under 20% AND network under 20% AND
memory under 20%, all three, across seven days. Several services resident in
memory will usually clear the memory threshold on their own, which frees the
core that keep-warm was burning. Check before relying on it:

     free -m        # used/total above 20%?
