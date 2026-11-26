# Include inside the official HTTPS static site's server block (not the intake listener).
# Exact dynamic-feed locations continue to take precedence.
location ~ ^/data/[A-Za-z0-9-]+[.]json$ {
    gzip on;
    gzip_types application/json;
    gzip_min_length 1024;
    gzip_comp_level 5;
    gzip_vary on;
    gzip_proxied any;
    try_files $uri =404;
}
