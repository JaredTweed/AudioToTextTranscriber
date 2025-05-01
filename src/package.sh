set -euo pipefail

# 1) Remove any lines containing "stdc++fs" from all CMakeLists.txt files
find . -name CMakeLists.txt -exec sed -i '/stdc++fs/d' {} \;

# 2) Create a zip archive named v1.0.0.zip containing everything in this dir
#    but exclude the archive itself
zip -r v1.0.0.zip . \
    -x v1.0.0.zip \
    -x "$(basename "$0")"
