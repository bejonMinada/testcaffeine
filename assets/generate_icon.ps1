Add-Type -AssemblyName System.Drawing

function New-CoffeeBitmap {
	param([int]$Size)

	$bitmap = New-Object System.Drawing.Bitmap $Size, $Size
	$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
	$graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
	$graphics.Clear([System.Drawing.Color]::FromArgb(245, 239, 232))

	$scale = $Size / 256.0
	$S = {
		param([double]$Value)
		[int][Math]::Round($Value * $scale)
	}

	$bgBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(141, 90, 59))
	$bgPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(108, 67, 45), [Math]::Max(1, (& $S 10)))
	$graphics.FillEllipse($bgBrush, (& $S 18), (& $S 18), (& $S 220), (& $S 220))
	$graphics.DrawEllipse($bgPen, (& $S 18), (& $S 18), (& $S 220), (& $S 220))

	$steamPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(247, 231, 216), [Math]::Max(1, (& $S 8)))
	$graphics.DrawArc($steamPen, (& $S 84), (& $S 58), (& $S 24), (& $S 60), 70, 220)
	$graphics.DrawArc($steamPen, (& $S 112), (& $S 50), (& $S 24), (& $S 60), 80, 220)
	$graphics.DrawArc($steamPen, (& $S 140), (& $S 58), (& $S 24), (& $S 60), 70, 220)

	$cupBrush = New-Object System.Drawing.SolidBrush ([System.Drawing.Color]::FromArgb(255, 245, 235))
	$cupPen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(95, 56, 34), [Math]::Max(1, (& $S 8)))
	$graphics.FillRectangle($cupBrush, (& $S 78), (& $S 124), (& $S 100), (& $S 58))
	$graphics.DrawRectangle($cupPen, (& $S 78), (& $S 124), (& $S 100), (& $S 58))
	$graphics.DrawArc($cupPen, (& $S 166), (& $S 132), (& $S 48), (& $S 36), -80, 160)

	$linePen = New-Object System.Drawing.Pen ([System.Drawing.Color]::FromArgb(95, 56, 34), [Math]::Max(1, (& $S 6)))
	$graphics.DrawLine($linePen, (& $S 66), (& $S 188), (& $S 190), (& $S 188))

	$bgBrush.Dispose()
	$bgPen.Dispose()
	$steamPen.Dispose()
	$cupBrush.Dispose()
	$cupPen.Dispose()
	$linePen.Dispose()
	$graphics.Dispose()

	return $bitmap
}

function New-SingleIconRecord {
	param([int]$Size)

	$bmp = New-CoffeeBitmap -Size $Size
	$icon = [System.Drawing.Icon]::FromHandle($bmp.GetHicon())

	$stream = New-Object System.IO.MemoryStream
	$icon.Save($stream)
	$bytes = $stream.ToArray()

	$width = $bytes[6]
	$height = $bytes[7]
	$planes = [BitConverter]::ToUInt16($bytes, 10)
	$bitCount = [BitConverter]::ToUInt16($bytes, 12)
	$bytesInRes = [BitConverter]::ToUInt32($bytes, 14)
	$imageOffset = [BitConverter]::ToUInt32($bytes, 18)
	$imageData = New-Object byte[] $bytesInRes
	[Array]::Copy($bytes, $imageOffset, $imageData, 0, $bytesInRes)

	$stream.Dispose()
	[System.Runtime.InteropServices.Marshal]::Release($icon.Handle) | Out-Null
	$icon.Dispose()
	$bmp.Dispose()

	[PSCustomObject]@{
		Width = $width
		Height = $height
		Planes = $planes
		BitCount = $bitCount
		ImageData = $imageData
	}
}

$sizes = @(16, 24, 32, 48, 64, 128, 256)
$records = @()
foreach ($size in $sizes) {
	$records += New-SingleIconRecord -Size $size
}

$iconPath = Join-Path $PSScriptRoot "testcaffeine.ico"
$fileStream = [System.IO.File]::Open($iconPath, [System.IO.FileMode]::Create)
$writer = New-Object System.IO.BinaryWriter($fileStream)

$writer.Write([UInt16]0)
$writer.Write([UInt16]1)
$writer.Write([UInt16]$records.Count)

$offset = 6 + (16 * $records.Count)
foreach ($record in $records) {
	$writer.Write([byte]$record.Width)
	$writer.Write([byte]$record.Height)
	$writer.Write([byte]0)
	$writer.Write([byte]0)
	$writer.Write([UInt16]$record.Planes)
	$writer.Write([UInt16]$record.BitCount)
	$writer.Write([UInt32]$record.ImageData.Length)
	$writer.Write([UInt32]$offset)
	$offset += $record.ImageData.Length
}

foreach ($record in $records) {
	$writer.Write($record.ImageData)
}

$writer.Flush()
$writer.Close()
$fileStream.Close()

Write-Host "Generated multi-size icon: $iconPath"
