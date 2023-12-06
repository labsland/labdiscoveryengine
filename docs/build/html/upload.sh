VERSION=0.0.2
echo "Uploading everything to version $VERSION"
aws s3 sync --acl public-read . s3://developers.labsland.com/labdiscoveryengine/en/$VERSION/
aws s3 sync --acl public-read . s3://developers.labsland.com/labdiscoveryengine/en/stable/
aws cloudfront create-invalidation --distribution-id=E1C70C27Q56411 --paths "/labdiscoveryengine/en/$VERSION/"
aws cloudfront create-invalidation --distribution-id=E1C70C27Q56411 --paths "/labdiscoveryengine/en/stable/"

