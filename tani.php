<?php
// tani.php - "ilan gelmiyor" sorununun nedenini tek sayfada gosterir. Sadece localhost.
header('Content-Type: text/plain; charset=utf-8');
header('Cache-Control: no-store');
if (!in_array($_SERVER['REMOTE_ADDR'] ?? '', ['127.0.0.1', '::1'], true)) {
    http_response_code(403);
    exit('yasak');
}
require __DIR__ . DIRECTORY_SEPARATOR . 'ortak.php';

$dir = __DIR__;
echo "KamuRadar tani raporu\n";
echo "=====================\n";
echo "PHP: " . PHP_VERSION . " / " . PHP_OS_FAMILY . "\n";
echo "Klasor: " . $dir . "\n";
$dis = ini_get('disable_functions');
echo "disable_functions: " . ($dis === '' ? '(bos)' : $dis) . "\n";
echo "popen: " . (function_exists('popen') ? 'acik' : 'KAPALI') . "\n";
echo "exec: " . (function_exists('exec') ? 'acik' : 'KAPALI') . "\n";
echo "Klasore yazilabilir mi: " . (is_writable($dir) ? 'evet' : 'HAYIR') . "\n\n";

$py = kr_python_bul('');
if ($py['cmd'] === null) {
    echo "PYTHON: BULUNAMADI\n";
    foreach ($py['notlar'] as $n) { echo "  denenen: " . $n . "\n"; }
} else {
    echo "PYTHON: " . $py['cmd'] . " -> " . $py['surum'] . "\n";
    foreach (['requests', 'truststore', 'pypdf'] as $p) {
        list($var, $o) = kr_paket_var($py['cmd'], $p);
        echo "  paket " . $p . ": " . ($var ? 'kurulu' : 'YOK  (komut: ' . $py['cmd'] . ' -m pip install ' . $p . ')') . "\n";
    }
}
echo "\n";

$j = $dir . DIRECTORY_SEPARATOR . 'ilanlar.json';
if (is_file($j)) {
    echo "ilanlar.json: " . filesize($j) . " bayt\n";
    $d = json_decode((string)file_get_contents($j), true);
    if (is_array($d)) {
        echo "  ilan sayisi: " . count($d['ilanlar'] ?? []) . "\n";
        echo "  son guncelleme: " . ($d['guncelleme'] ?? 'yok') . "\n";
        $st = [];
        foreach (($d['ilanlar'] ?? []) as $r) {
            $s = $r['source_name'] ?? '?';
            if (!isset($st[$s])) { $st[$s] = ['n' => 0, 'puan' => 0, 'sehir' => 0, 'tarih' => 0, 'egitim' => 0]; }
            $st[$s]['n']++;
            if (!empty($r['min_kpss'])) { $st[$s]['puan']++; }
            if (!empty($r['city'])) { $st[$s]['sehir']++; }
            if (!empty($r['deadline'])) { $st[$s]['tarih']++; }
            if (!empty($r['education_level'])) { $st[$s]['egitim']++; }
        }
        foreach ($st as $s => $v) {
            echo "  [" . $s . "] ilan " . $v['n'] . " | puani okunan " . $v['puan'] . " | sehri okunan " . $v['sehir']
               . " | son tarihi okunan " . $v['tarih'] . " | egitimi okunan " . $v['egitim'] . "\n";
        }
        foreach (($d['kaynaklar'] ?? []) as $ad => $k) {
            echo "  kaynak " . $ad . ": " . ($k['durum'] ?? '?') . " (" . ($k['sayi'] ?? 0) . ")"
               . (isset($k['mesaj']) ? " - " . $k['mesaj'] : '') . "\n";
        }
    } else {
        echo "  UYARI: dosya okunamadi (bozuk JSON)\n";
    }
} else {
    echo "ilanlar.json: YOK\n";
}
echo "\nguncelle.lock: " . (is_file($dir . DIRECTORY_SEPARATOR . 'guncelle.lock') ? 'var (calisiyor ya da yarida kalmis)' : 'yok') . "\n";
echo "son_guncelleme.txt: " . (is_file($dir . DIRECTORY_SEPARATOR . 'son_guncelleme.txt') ? 'var (son calisma basarili)' : 'yok') . "\n\n";

$bt = $dir . DIRECTORY_SEPARATOR . 'belge_tani.txt';
echo "belge_tani.txt:\n" . (is_file($bt) ? file_get_contents($bt) : "(yok: Kamu Ilan henuz calismamis)\n") . "\n";

$log = $dir . DIRECTORY_SEPARATOR . 'ilan_cek_log.txt';
echo "ilan_cek_log.txt (son 40 satir):\n";
if (is_file($log)) {
    $satirlar = file($log, FILE_IGNORE_NEW_LINES);
    echo implode("\n", array_slice($satirlar, -40)) . "\n";
} else {
    echo "(log yok: Python hic calistirilmamis demek)\n";
}
