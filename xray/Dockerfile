# Offline fallback: place the checksum-verified official Linux x64 binary here.
# This minimal image is for the supplied security=none + VLESS-encrypted config.
FROM scratch
COPY xray /usr/local/bin/xray
ENTRYPOINT ["/usr/local/bin/xray"]
