param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd")
)

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$VaultRoot = $RepoRoot.Path
$DateRoot = Join-Path $VaultRoot "20_视频卡片\$Date"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$Buckets = @("01_方法样本", "02_方法线索待验证", "80_人物经历素材", "81_争议风险素材", "90_低价值待复核")

if (-not (Test-Path -LiteralPath $DateRoot -PathType Container)) {
    Write-Host "No video-card folder for date: $Date"
    exit 0
}

function Get-RelativePath([string]$FullPath) {
    return $FullPath.Substring($VaultRoot.Length + 1).Replace("\", "/")
}

function Normalize-RelativePath([string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return ""
    }
    return $Path.Replace("\", "/")
}

function ConvertTo-Count([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return 0
    }
    $clean = $Value.Trim().Trim('"')
    $number = 0
    if ([int]::TryParse($clean, [ref]$number)) {
        return $number
    }
    return 0
}

function Get-FrontMatterValue([string]$Text, [string]$Key) {
    $match = [regex]::Match($Text, "(?m)^$([regex]::Escape($Key)):\s*(.*?)\s*$")
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}

function Get-Section([string]$Text, [string]$Name) {
    $pattern = "(?ms)^##\s+$([regex]::Escape($Name))\s*\r?\n(.*?)(?=^##\s+|\z)"
    $match = [regex]::Match($Text, $pattern)
    if ($match.Success) {
        return $match.Groups[1].Value.Trim()
    }
    return ""
}

function Get-CardScores([string]$Path) {
    $text = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $body = @(
        (Get-Section $text "一句话结论"),
        (Get-Section $text "从教学到复盘/行动的映射"),
        (Get-Section $text "AI 摘要"),
        (Get-Section $text "原始文本")
    ) -join "`n"

    $methodWords = @(
        "买点", "卖点", "买入", "卖出", "止损", "仓位", "分仓", "满仓", "空仓", "回撤",
        "打板", "低吸", "半路", "追涨", "反包", "弱转强", "高低切", "补涨", "卡位",
        "首板", "二板", "连板", "龙头", "中军", "题材", "主线", "竞价", "集合竞价",
        "涨停", "跌停", "炸板", "烂板", "换手", "放量", "缩量", "承接", "分歧", "一致",
        "分时", "量价", "盘口", "筹码", "情绪周期", "退潮", "修复", "冰点", "主升",
        "复盘", "交割单", "模式", "体系", "战法", "看盘", "大单", "一字板"
    )
    $bioWords = @(
        "人物", "简介", "本名", "出生", "毕业", "学历", "传奇", "故事", "经历", "入市",
        "本金", "资产", "身家", "逆袭", "崛起", "财富", "大佬", "江湖", "妻", "神话",
        "采访", "专访"
    )
    $riskWords = @("被罚", "监管", "处罚", "内幕", "争议", "退网", "亏光", "腰斩", "爆仓", "轻生")

    $method = 0
    $bio = 0
    $risk = 0
    foreach ($word in $methodWords) {
        if ($body -match [regex]::Escape($word)) { $method++ }
    }
    foreach ($word in $bioWords) {
        if ($body -match [regex]::Escape($word)) { $bio++ }
    }
    foreach ($word in $riskWords) {
        if ($body -match [regex]::Escape($word)) { $risk++ }
    }

    return [pscustomobject]@{
        Method = $method
        Bio = $bio
        Risk = $risk
        Body = $body
    }
}

function Get-CardPlacement([string]$Path) {
    $scores = Get-CardScores $Path
    $fullText = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
    $isSearchSnippet = $fullText -match "搜索采集|来源:\s*https://so\.douyin\.com|来源链接：https://so\.douyin\.com"
    $isVerifiedPrimarySource = $fullText -match "原视频已回看:\s*true|逐字稿已核验:\s*true|来源层级:\s*一手心得|可信度:\s*A"
    $hasTranscript = $fullText -match "(?ms)^##\s+对白文本\s*\r?\n\s*(?!暂无\s*(?:\r?\n|$)).{20,}"
    $base = [System.IO.Path]::GetFileNameWithoutExtension($Path)
    $topic = $base -replace "^\d+_", ""
    $relativeToDate = $Path.Substring($DateRoot.Length + 1)
    $segments = $relativeToDate.Split("\")
    $currentBucket = $segments[0]
    $currentPerson = ""
    if ($Buckets -contains $currentBucket -and $segments.Count -ge 3) {
        $person = $segments[1]
        $currentPerson = $segments[1]
    }
    else {
        $person = (Split-Path (Split-Path $Path -Parent) -Leaf)
        $currentPerson = $person
        $currentBucket = ""
    }
    $person = Get-TopicFolder $fullText $person

    $strongTopic = "弱转强|高低切|补涨卡位|反包战法|集合竞价|盘口承接|出货视角|容量核心|龙头战法|首板模式|二板定龙头|低吸模式|交割单复盘|中国医药案例|复盘案例"
    $softTopic = "交易心法|操作模式|风控回撤|止损|板块理解"
    $bioTopic = "成长经历|人物简介|监管处罚|视频摘要|龙虎榜席位|席位大盘点|稳定风格|激流勇退|交易边界|敢死队|总舵主|往事|点石成金|牛股筛选|30%案例|格局王|天赋与门槛|被点醒经历|大众一战争议|交易哲学|今日分享|跌停案例|寒武纪|\d+(?:元|万|亿)?到\d+|\d+万\d+个月\d+倍|\d+年.*\d+倍"

    $bucket = "90_低价值待复核"
    $type = "低价值待复核"
    $grade = "D"

    if ($topic -match $bioTopic) {
        if ($scores.Risk -gt 0) {
            $bucket = "81_争议风险素材"
            $type = "争议风险素材"
        }
        elseif ($scores.Bio -gt 0) {
            $bucket = "80_人物经历素材"
            $type = "人物经历素材"
            $grade = "C"
        }
    }
    elseif ($topic -match $strongTopic -and $scores.Method -ge 3) {
        $bucket = "01_方法样本"
        $type = "交易方法样本"
        $grade = "A"
    }
    elseif (
        $topic -match $softTopic -and
        $scores.Method -ge 4 -and
        $scores.Bio -le 3 -and
        $scores.Body -match "原则|纪律|只做|等待|仓位|止损|执行|分歧|一致|买|卖|复盘|承接|换手|题材|龙头"
    ) {
        $bucket = "01_方法样本"
        $type = "心法纪律样本"
        $grade = "B"
    }
    elseif ($scores.Method -ge 4 -and $scores.Bio -le 2) {
        $bucket = "02_方法线索待验证"
        $type = "待验证方法线索"
        $grade = "B-"
    }
    elseif ($scores.Risk -gt 0) {
        $bucket = "81_争议风险素材"
        $type = "争议风险素材"
    }
    elseif ($scores.Bio -gt 0) {
        $bucket = "80_人物经历素材"
        $type = "人物经历素材"
        $grade = "C"
    }

    if ($bucket -eq "01_方法样本") {
        $evidenceTerms = @(
            "前一天", "次日", "高开", "放量", "换手", "烂板", "大长腿", "大单一字",
            "9:15", "9：15", "9:20", "9：20", "9:25", "9：25", "隔夜单", "红柱",
            "诱多", "诱空", "分时", "黄线", "均价线", "5日", "五日线", "十日线",
            "主升浪", "分歧转一致", "一致转分歧", "仓位", "止损", "主线", "退潮",
            "冰点", "二板", "一进二", "二进三", "卡位", "分离", "龙一", "龙二",
            "换手龙头", "低位", "高位", "承接", "缩量", "炸板", "回封", "盈亏比",
            "只做", "空仓", "买在", "卖在", "回踩", "封单", "高度板", "连板梯队",
            "题材", "情绪周期"
        )
        $evidenceCount = 0
        foreach ($term in $evidenceTerms) {
            if ($scores.Body -match [regex]::Escape($term)) {
                $evidenceCount++
            }
        }

        $hasSpecificMethodDetail = $scores.Body -match "卖出信号|跌破|巨量阴线|成交量前20|主流题材|重大利好|铁律口诀|模式之外|高位巨量|均线多头"
        $knownThinPattern = $scores.Body -match "十大游资操盘手法&成名绝学|存储有哪些核心龙头|集合竞价游资如何抓涨停|龙头战法 每日分享炒股战法"

        if ($knownThinPattern) {
            $bucket = "90_低价值待复核"
            $type = "低价值待复核"
            $grade = "D"
        }
        elseif ($evidenceCount -lt 2 -and -not $hasSpecificMethodDetail) {
            $bucket = "02_方法线索待验证"
            $type = "待验证方法线索"
            $grade = "B-"
        }
    }

    if ($bucket -eq "01_方法样本" -and (-not $isVerifiedPrimarySource -or -not $hasTranscript)) {
        $bucket = "02_方法线索待验证"
        $type = "待验证方法线索"
        $grade = "B-"
    }

    return [pscustomobject]@{
        Path = $Path
        CurrentBucket = $currentBucket
        CurrentPerson = $currentPerson
        Bucket = $bucket
        Person = $person
        Type = $type
        Grade = $grade
        Method = $scores.Method
        Bio = $scores.Bio
    }
}

function Get-TopicFolder([string]$Text, [string]$Fallback) {
    $haystack = $Text
    $knownNames = @(
        "92科比", "A神-Asking", "北京炒家", "炒股养家", "陈小群", "方新侠", "佛山无影脚",
        "古北路", "呼家楼", "欢乐海岸", "交易猿", "金田路", "毛老板", "涅槃重升",
        "宁波桑田路", "乔帮主", "清扬路", "瑞鹤仙", "上塘路", "退学炒股", "消闲派",
        "小鳄鱼", "徐翔-宁波敢死队", "章盟主", "赵老哥", "职业炒手", "著名刺客", "作手新一"
    )
    foreach ($name in $knownNames) {
        if ($haystack -match [regex]::Escape($name)) {
            return $name
        }
    }
    if ($haystack -match "退神") { return "退学炒股" }
    if ($haystack -match "升大|重升") { return "涅槃重升" }
    if ($haystack -match "群总") { return "陈小群" }
    if ($haystack -match "校长|王涛") { return "职业炒手" }
    if ($haystack -match "总舵主|宁波敢死队") { return "徐翔-宁波敢死队" }
    if ($haystack -match "集合竞价|竞价") { return "技术主题-竞价" }
    if ($haystack -match "弱转强") { return "技术主题-弱转强" }
    if ($haystack -match "高低切") { return "技术主题-高低切" }
    if ($haystack -match "补涨|卡位") { return "技术主题-补涨卡位" }
    if ($haystack -match "盘口|承接") { return "技术主题-盘口承接" }
    if ($haystack -match "反包") { return "技术主题-反包" }
    if ($haystack -match "容量核心") { return "技术主题-容量核心" }
    if ($Fallback -match "^\d{4}-\d{2}-\d{2}$") {
        return "未归类线索"
    }
    return $Fallback
}

function Update-FrontMatter([string]$Text, [string]$Type, [string]$Grade, [int]$Method, [int]$Bio) {
    $fields = @(
        "内容分层: $Type",
        "方法价值: $Grade",
        "方法信号分: $Method",
        "人物故事分: $Bio"
    )

    if ($Text -match "(?s)^---\r?\n(.*?)\r?\n---") {
        $frontMatter = $matches[1]
        foreach ($key in @("内容分层", "方法价值", "方法信号分", "人物故事分")) {
            $frontMatter = [regex]::Replace($frontMatter, "(?m)^${key}:.*\r?\n?", "")
        }
        $newFrontMatter = $frontMatter.TrimEnd() + "`r`n" + ($fields -join "`r`n")
        return [regex]::Replace($Text, "(?s)^---\r?\n.*?\r?\n---", "---`r`n$newFrontMatter`r`n---", 1)
    }

    return "---`r`n$($fields -join "`r`n")`r`n---`r`n`r`n$Text"
}

function Get-UniqueTarget([string]$Dir, [string]$Name) {
    $target = Join-Path $Dir $Name
    if (-not (Test-Path -LiteralPath $target)) {
        return $target
    }
    $stem = [System.IO.Path]::GetFileNameWithoutExtension($Name)
    $extension = [System.IO.Path]::GetExtension($Name)
    $i = 2
    do {
        $target = Join-Path $Dir "$stem-$i$extension"
        $i++
    } while (Test-Path -LiteralPath $target)
    return $target
}

$placements = @(Get-ChildItem -LiteralPath $DateRoot -Recurse -File -Filter "*.md" | ForEach-Object {
    Get-CardPlacement $_.FullName
})

$moves = @()
foreach ($placement in $placements) {
    $text = [System.IO.File]::ReadAllText($placement.Path, [System.Text.Encoding]::UTF8)
    $text = Update-FrontMatter $text $placement.Type $placement.Grade $placement.Method $placement.Bio
    [System.IO.File]::WriteAllText($placement.Path, $text, $Utf8NoBom)

    if ($placement.CurrentBucket -ne $placement.Bucket -or $placement.CurrentPerson -ne $placement.Person) {
        $targetDir = Join-Path (Join-Path $DateRoot $placement.Bucket) $placement.Person
        New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
        $target = Get-UniqueTarget $targetDir ([System.IO.Path]::GetFileName($placement.Path))
        $moves += [pscustomobject]@{
            OldFull = $placement.Path
            NewFull = $target
            OldRel = Get-RelativePath $placement.Path
            NewRel = Get-RelativePath $target
        }
    }
}

foreach ($move in $moves) {
    $resolved = (Resolve-Path -LiteralPath $move.OldFull).Path
    if (-not $resolved.StartsWith($VaultRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to move outside vault: $resolved"
    }
    Move-Item -LiteralPath $resolved -Destination $move.NewFull
}

$orderedMoves = @($moves | Sort-Object { $_.OldRel.Length } -Descending)
$statePath = Join-Path $VaultRoot ".state\processed.json"
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($property in @($state.processed.PSObject.Properties)) {
        foreach ($move in $orderedMoves) {
            if ((Normalize-RelativePath $property.Value) -eq (Normalize-RelativePath $move.OldRel)) {
                $property.Value = $move.NewRel
            }
        }
    }

    $cardsByHash = @{}
    foreach ($card in (Get-ChildItem -LiteralPath $DateRoot -Recurse -File -Filter "*.md")) {
        if ($card.BaseName -match "_([0-9a-f]{16})$") {
            $cardsByHash[$matches[1]] = Get-RelativePath $card.FullName
        }
    }
    foreach ($property in @($state.processed.PSObject.Properties)) {
        $relative = Normalize-RelativePath $property.Value
        $absolute = Join-Path $VaultRoot ($relative.Replace("/", "\"))
        if (Test-Path -LiteralPath $absolute) {
            $property.Value = $relative
        }
        elseif ($cardsByHash.ContainsKey($property.Name)) {
            $property.Value = $cardsByHash[$property.Name]
        }
    }
    [System.IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 100), $Utf8NoBom)
}

if ($orderedMoves.Count -gt 0) {
    $markdownFiles = Get-ChildItem -LiteralPath $VaultRoot -Recurse -File -Filter "*.md" |
        Where-Object { $_.FullName -notlike (Join-Path $VaultRoot ".state\*") }
    foreach ($file in $markdownFiles) {
        $text = [System.IO.File]::ReadAllText($file.FullName, [System.Text.Encoding]::UTF8)
        $newText = $text
        foreach ($move in $orderedMoves) {
            $oldStem = $move.OldRel -replace "\.md$", ""
            $newStem = $move.NewRel -replace "\.md$", ""
            $newText = $newText.Replace($oldStem, $newStem)
        }
        if ($newText -ne $text) {
            [System.IO.File]::WriteAllText($file.FullName, $newText, $Utf8NoBom)
        }
    }
}

foreach ($bucket in $Buckets) {
    $bucketDir = Join-Path $DateRoot $bucket
    if (Test-Path -LiteralPath $bucketDir) {
        Get-ChildItem -LiteralPath $bucketDir -Directory -Recurse |
            Sort-Object FullName -Descending |
            ForEach-Object {
                if (-not (Get-ChildItem -LiteralPath $_.FullName -Force | Select-Object -First 1)) {
                    Remove-Item -LiteralPath $_.FullName
                }
            }
    }
}

function Write-VideoIndex {
    $indexPath = Join-Path $VaultRoot "90_索引\抖音知识库索引.md"
    $cards = Get-ChildItem -LiteralPath $DateRoot -Recurse -File -Filter "*.md" | Sort-Object FullName
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# 抖音知识库索引")
    $lines.Add("")
    $lines.Add("已处理视频数：$($cards.Count)")
    $lines.Add("")
    $lines.Add("## 视频卡片")
    $lines.Add("")

    foreach ($bucket in $Buckets) {
        $bucketDir = Join-Path $DateRoot $bucket
        if (-not (Test-Path -LiteralPath $bucketDir)) {
            continue
        }
        $bucketCards = @(Get-ChildItem -LiteralPath $bucketDir -Recurse -File -Filter "*.md" | Sort-Object DirectoryName, Name)
        $lines.Add("## $bucket（$($bucketCards.Count)）")
        $lines.Add("")
        foreach ($group in ($bucketCards | Group-Object { $_.Directory.Name } | Sort-Object Name)) {
            $lines.Add("### $($group.Name)")
            foreach ($card in ($group.Group | Sort-Object Name)) {
                $rel = (Get-RelativePath $card.FullName) -replace "\.md$", ""
                $alias = [System.IO.Path]::GetFileNameWithoutExtension($card.Name)
                $lines.Add("- [[$rel|$alias]]")
            }
            $lines.Add("")
        }
    }

    [System.IO.File]::WriteAllText($indexPath, ($lines -join "`r`n"), $Utf8NoBom)
}

function Write-MethodIndex {
    $indexPath = Join-Path $VaultRoot "90_索引\交易方法样本索引.md"
    $methodRoot = Join-Path $DateRoot "01_方法样本"
    $cards = @()
    if (Test-Path -LiteralPath $methodRoot) {
        $cards = @(Get-ChildItem -LiteralPath $methodRoot -Recurse -File -Filter "*.md" | Sort-Object DirectoryName, Name)
    }

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# 交易方法样本索引")
    $lines.Add("")
    $lines.Add("只收录已核验的一手视频、回看记录或逐字稿样本。搜索结果摘要和搬运号标题只能进入待验证线索。")
    $lines.Add("")
    $lines.Add("样本数：$($cards.Count)")
    $lines.Add("")

    foreach ($group in ($cards | Group-Object { $_.Directory.Name } | Sort-Object Name)) {
        $lines.Add("## $($group.Name)")
        foreach ($card in ($group.Group | Sort-Object Name)) {
            $rel = (Get-RelativePath $card.FullName) -replace "\.md$", ""
            $alias = [System.IO.Path]::GetFileNameWithoutExtension($card.Name)
            $lines.Add("- [[$rel|$alias]]")
        }
        $lines.Add("")
    }

    [System.IO.File]::WriteAllText($indexPath, ($lines -join "`r`n"), $Utf8NoBom)
}

function Write-AuditReport {
    $auditDir = Join-Path $VaultRoot "50_内容审计"
    New-Item -ItemType Directory -Force -Path $auditDir | Out-Null
    $auditPath = Join-Path $auditDir "$Date`_视频卡片价值分层.md"
    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add("# $Date 视频卡片价值分层")
    $lines.Add("")
    $lines.Add('这次审计的核心结论：不能把《游资人物故事》或《搜索结果摘要》直接当成《交易方法》。正式方法库只吸收已回看原视频、逐字稿已核验或一手来源明确的样本。')
    $lines.Add("")
    $lines.Add("## 分层统计")
    $lines.Add("")
    foreach ($bucket in $Buckets) {
        $dir = Join-Path $DateRoot $bucket
        $count = 0
        if (Test-Path -LiteralPath $dir) {
            $count = (Get-ChildItem -LiteralPath $dir -Recurse -File -Filter "*.md").Count
        }
        $lines.Add("- $bucket：$count")
    }
    $lines.Add("")
    $lines.Add("## 目录规则")
    $lines.Add("")
    $lines.Add('- `01_方法样本`：已回看原视频、逐字稿已核验或一手来源明确，并且正文有条件、动作或纪律证据。')
    $lines.Add('- `02_方法线索待验证`：搜索摘要、搬运号标题或未经核验的方法线索，需要回看视频或二次采集。')
    $lines.Add('- `80_人物经历素材`：人物故事、成长经历、传记素材，只作背景。')
    $lines.Add('- `81_争议风险素材`：处罚、监管、争议、退网等风险素材。')
    $lines.Add('- `90_低价值待复核`：标题党、相关搜索、播放数据或没有足够方法细节的样本。')
    $lines.Add("")
    $lines.Add("## 下一轮采集过滤规则")
    $lines.Add("")
    $lines.Add('- 优先搜：游资名 + 交割单 / 模式 / 买点 / 卖点 / 仓位 / 止损 / 复盘 / 竞价 / 承接 / 弱转强 / 龙头 / 低吸 / 打板。')
    $lines.Add('- 降权：人物介绍 / 传奇 / 从几万到几亿 / 逆袭 / 身家 / 采访 / 被罚 / 江湖故事。')
    $lines.Add("- 每条视频进入方法库前，至少要能回答：盘面条件是什么、动作是什么、失效信号是什么、如何复盘验证。")
    [System.IO.File]::WriteAllText($auditPath, ($lines -join "`r`n"), $Utf8NoBom)
}

Write-VideoIndex
Write-MethodIndex
Write-AuditReport

Write-Host "Curated $Date video cards."
foreach ($bucket in $Buckets) {
    $dir = Join-Path $DateRoot $bucket
    $count = 0
    if (Test-Path -LiteralPath $dir) {
        $count = (Get-ChildItem -LiteralPath $dir -Recurse -File -Filter "*.md").Count
    }
    Write-Host "$bucket=$count"
}
