# All downloads, package installs and installer launches below are mocked.
. "$PSScriptRoot\..\setup_ocr_helpers.ps1"

Describe 'Tesseract installation recovery' {
    BeforeEach {
        $script:readerInstalled = $false
        Mock Find-SmartDocTesseract { if ($script:readerInstalled) { 'C:\Synthetic User\Tesseract-OCR\tesseract.exe' } }
        Mock Install-SmartDocTesseractWithWinget { }
        Mock Install-SmartDocTesseractDirect { $script:readerInstalled = $true }
    }
    It 'reuses an existing reader without installing anything' {
        $script:readerInstalled = $true
        Get-OrInstallSmartDocTesseract | Should Be 'C:\Synthetic User\Tesseract-OCR\tesseract.exe'
        Assert-MockCalled Install-SmartDocTesseractWithWinget -Times 0 -Exactly -Scope It
        Assert-MockCalled Install-SmartDocTesseractDirect -Times 0 -Exactly -Scope It
    }
    It 'falls back when the package installer is missing' {
        Get-OrInstallSmartDocTesseract | Should Be 'C:\Synthetic User\Tesseract-OCR\tesseract.exe'
        Assert-MockCalled Install-SmartDocTesseractDirect -Times 1 -Exactly -Scope It
    }
    It 'falls back when the package installer throws an error' {
        Mock Install-SmartDocTesseractWithWinget { throw 'Synthetic unavailable package service' }
        Get-OrInstallSmartDocTesseract | Should Be 'C:\Synthetic User\Tesseract-OCR\tesseract.exe'
        Assert-MockCalled Install-SmartDocTesseractDirect -Times 1 -Exactly -Scope It
    }
    It 'keeps a successful package install' {
        Mock Install-SmartDocTesseractWithWinget { $script:readerInstalled = $true }
        Get-OrInstallSmartDocTesseract | Should Be 'C:\Synthetic User\Tesseract-OCR\tesseract.exe'
        Assert-MockCalled Install-SmartDocTesseractDirect -Times 0 -Exactly -Scope It
    }
    It 'fails if neither route actually installs the reader' {
        Mock Install-SmartDocTesseractDirect { }
        { Get-OrInstallSmartDocTesseract } | Should Throw 'OCR setup is incomplete'
    }
}

Describe 'Verified direct installer' {
    BeforeEach {
        Mock Invoke-WebRequest { }
        Mock Get-FileHash { [pscustomobject]@{Hash='bee9e3434bd94fd65387d9be28cd467a41f61b1275383b55b0f59a1331270ae4'} }
        Mock Start-Process { [pscustomobject]@{ExitCode=0} }
        Mock Remove-Item { }
    }
    It 'never executes a download whose checksum differs' {
        Mock Get-FileHash { [pscustomobject]@{Hash='incorrect'} }
        { Install-SmartDocTesseractDirect } | Should Throw 'verification failed'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
        Assert-MockCalled Remove-Item -Times 1 -Exactly -Scope It
    }
    It 'handles a user directory containing spaces and launches without a console' {
        $savedAppData = $env:LOCALAPPDATA
        try {
            $env:LOCALAPPDATA = 'C:\Users\Synthetic User\AppData\Local'
            Install-SmartDocTesseractDirect
            Assert-MockCalled Start-Process -Times 1 -Exactly -Scope It -ParameterFilter {
                $WindowStyle -eq 'Hidden' -and $Wait -and $PassThru -and
                $ArgumentList[0] -eq '/S' -and $ArgumentList[1] -eq '/CURRENTUSER' -and
                $ArgumentList[-1] -eq '/D=C:\Users\Synthetic User\AppData\Local\Programs\Tesseract-OCR'
            }
        } finally { $env:LOCALAPPDATA = $savedAppData }
    }
    It 'reports a failed installer instead of success' {
        Mock Start-Process { [pscustomobject]@{ExitCode=1603} }
        { Install-SmartDocTesseractDirect } | Should Throw 'installation failed'
        Assert-MockCalled Remove-Item -Times 1 -Exactly -Scope It
    }
    It 'does not run an installer after a failed download' {
        Mock Invoke-WebRequest { throw 'Synthetic connection failure' }
        { Install-SmartDocTesseractDirect } | Should Throw 'connection failure'
        Assert-MockCalled Start-Process -Times 0 -Exactly -Scope It
    }
}

Describe 'Language verification' {
    function Invoke-SyntheticTesseract {
        $global:LASTEXITCODE = $script:readerExit
        $script:readerLanguages
    }
    BeforeEach {
        $script:readerExit = 0
        $script:readerLanguages = @('List of available languages (3):', 'eng', 'osd', 'swe')
    }
    It 'accepts a working reader with every required language' {
        { Assert-SmartDocOcrLanguages -Executable Invoke-SyntheticTesseract -LanguageDirectory 'C:\Synthetic Data' } | Should Not Throw
    }
    It 'rejects a reader missing Swedish data even if its exit code is zero' {
        $script:readerLanguages = @('eng', 'osd')
        { Assert-SmartDocOcrLanguages -Executable Invoke-SyntheticTesseract -LanguageDirectory 'C:\Synthetic Data' } | Should Throw "'swe' is missing"
    }
    It 'rejects language loading errors' {
        $script:readerExit = 1
        { Assert-SmartDocOcrLanguages -Executable Invoke-SyntheticTesseract -LanguageDirectory 'C:\Synthetic Data' } | Should Throw 'could not be loaded'
    }
}
