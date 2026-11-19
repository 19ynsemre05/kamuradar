<?php
// ortak.php - guncelle.php ve tani.php icin ortak yardimcilar

function kr_calistir($cmd) {
    $cikti = [];
    $kod = 1;
    if (!function_exists('exec')) {
        return [1, 'exec() kapali (php.ini disable_functions)'];
    }
    @exec($cmd . ' 2>&1', $cikti, $kod);
    return [$kod, trim(implode("\n", $cikti))];
}

// Calisan bir Python bulur. Windows Magaza "kisayolu" (gercek Python degil) elenir.
function kr_python_bul($ayar = '') {
    $win = (PHP_OS_FAMILY === 'Windows');
    if ($ayar !== '') {
        $adaylar = [$ayar];
    } else {
        $adaylar = $win ? ['python', 'py -3', 'py'] : ['python3', 'python'];
    }
    $notlar = [];
    foreach ($adaylar as $c) {
        list($kod, $o) = kr_calistir($c . ' --version');
        if ($kod === 0 && stripos($o, 'Python') === 0 && stripos($o, 'not found') === false) {
            return ['cmd' => $c, 'surum' => $o, 'notlar' => $notlar];
        }
        $notlar[] = $c . ' --version -> kod ' . $kod . ': ' . substr($o, 0, 120);
    }
    return ['cmd' => null, 'surum' => null, 'notlar' => $notlar];
}

function kr_paket_var($cmd, $paket) {
    list($kod, $o) = kr_calistir($cmd . ' -c "import ' . $paket . '"');
    return [$kod === 0, $o];
}
