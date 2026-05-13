#!/bin/bash
# 批量下载 SEC 财报脚本
# 用法: ./batch_download.sh AAPL MSFT NVDA
# 用法: ./batch_download.sh -f tickers.txt
# 用法: ./batch_download.sh -y 3 AAPL MSFT  # 只下载近3年

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SEC_SCRIPT="$SCRIPT_DIR/sec_filings.py"
DEFAULT_YEARS=5
TICKERS=()
YEARS=$DEFAULT_YEARS
FROM_FILE=""

# 外国私人发行人（申报 20-F / 6-K 而非 10-K / 10-Q）
FOREIGN_ISSUERS="PDD BABA JD TME BIDU NIO XPEV LI ZTO BZ CNKNY GDS VNET ATHM LAIX YMM DADA GCT KC"

is_foreign_issuer() {
    local ticker=$(echo "$1" | tr '[:lower:]' '[:upper:]')
    for f in $FOREIGN_ISSUERS; do
        [[ "$ticker" == "$f" ]] && return 0
    done
    return 1
}

usage() {
    echo "用法: $0 [选项] ticker1 ticker2 ..."
    echo ""
    echo "选项:"
    echo "  -f, --file FILE    从文件读取 ticker 列表（每行一个）"
    echo "  -y, --years N      下载近 N 年的财报（默认 5）"
    echo "  -h, --help         显示帮助"
    echo ""
    echo "示例:"
    echo "  $0 AAPL MSFT NVDA"
    echo "  $0 -f my_tickers.txt"
    echo "  $0 -y 3 AAPL TSLA"
    echo "  $0 -f tickers.txt -y 10"
}

while [[ $# -gt 0 ]]; do
    case $1 in
        -f|--file)
            FROM_FILE="$2"
            shift 2
            ;;
        -y|--years)
            YEARS="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        -*)
            echo "未知选项: $1"
            usage
            exit 1
            ;;
        *)
            TICKERS+=("$1")
            shift
            ;;
    esac
done

# 从文件读取 tickers
if [[ -n "$FROM_FILE" ]]; then
    if [[ ! -f "$FROM_FILE" ]]; then
        echo "错误: 文件不存在: $FROM_FILE"
        exit 1
    fi
    while IFS= read -r line; do
        line=$(echo "$line" | tr -d '[:space:]')
        [[ -z "$line" || "$line" == \#* ]] && continue
        TICKERS+=("$line")
    done < "$FROM_FILE"
fi

if [[ ${#TICKERS[@]} -eq 0 ]]; then
    echo "错误: 请提供至少一个 ticker"
    usage
    exit 1
fi

echo "=========================================="
echo "  SEC 财报批量下载"
echo "  公司数: ${#TICKERS[@]}"
echo "  年限: ${YEARS} 年"
echo "  列表: ${TICKERS[*]}"
echo "=========================================="

SUCCESS=0
FAILED=0
FAILED_LIST=()

for ticker in "${TICKERS[@]}"; do
    echo ""
    echo ">>> [$(( SUCCESS + FAILED + 1 ))/${#TICKERS[@]}] 下载 $ticker ..."

    # 外国私人发行人用 20-F/6-K，美国本土公司用 10-K/10-Q
    if is_foreign_issuer "$ticker"; then
        echo "  (外国公司: 使用 20-F + 6-K 格式)"
        CMD_ARGS=(download "$ticker" --form "20-F,6-K" --years "$YEARS")
    else
        CMD_ARGS=(download "$ticker" --years "$YEARS")
    fi

    if python3 "$SEC_SCRIPT" "${CMD_ARGS[@]}"; then
        SUCCESS=$(( SUCCESS + 1 ))
        echo "✅ $ticker 下载完成"
    else
        FAILED=$(( FAILED + 1 ))
        FAILED_LIST+=("$ticker")
        echo "❌ $ticker 下载失败"
    fi
    # SEC 限流保护：间隔 1 秒
    sleep 1
done

echo ""
echo "=========================================="
echo "  下载完成"
echo "  成功: $SUCCESS"
echo "  失败: $FAILED"
if [[ $FAILED -gt 0 ]]; then
    echo "  失败列表: ${FAILED_LIST[*]}"
fi
echo "=========================================="
