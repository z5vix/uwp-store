/* xboxbridge.exe: lets the store use wine-uwp's Xbox Live sign-in (winexboxauth.dll) from outside the Xbox app.
 *
 *   xboxbridge import            stdin: refresh token, then the account's user id, one per line
 *   xboxbridge get URL [CONTRACT] prints "STATUS <http status>" and the body of a signed, authorized GET
 *   xboxbridge keys CONTENTID [MARKET]
 *                                 prints an encrypted package's content keys ("kid:key;...") from the Store licence
 *   xboxbridge owns PRODUCTID     prints "owned" or "not owned"
 *   xboxbridge importclient CLIENTID
 *                                 stdin: a refresh token for a game's own Microsoft client (MicrosoftGame.Config MSAAppId)
 *   xboxbridge hasclient CLIENTID prints "yes" when the signed-in account has that client's sign-in, else "no"
 *
 * Secrets travel only through stdin and stdout pipes. Build: x86_64-w64-mingw32-gcc -O2 -o xboxbridge.exe
 * xboxbridge.c -lole32 (the store builds it on first use when a copy is missing). Exit status 0 on success. */

#include <windows.h>
#include <objbase.h>
#include <stdio.h>
#include <io.h>
#include <fcntl.h>
#include <string.h>

typedef HRESULT (WINAPI *import_fn)( const char *, const char * );
typedef HRESULT (WINAPI *get_fn)( const char *, const char *, const char *, unsigned char **, UINT32 *, DWORD * );
typedef HRESULT (WINAPI *keys_fn)( const char *, const char *, char *, UINT32 );
typedef HRESULT (WINAPI *owns_fn)( const char * );
typedef HRESULT (WINAPI *import_client_fn)( const char *, const char * );
typedef HRESULT (WINAPI *has_client_fn)( const char * );

static HMODULE auth;

static FARPROC need( const char *name )
{
    FARPROC fn = auth ? GetProcAddress( auth, name ) : NULL;
    if (!fn)
    {
        fprintf( stderr, "xboxbridge: this wine-uwp has no winexboxauth.dll %s\n", name );
        exit( 3 );
    }
    return fn;
}

static void chomp( char *s )
{
    s[strcspn( s, "\r\n" )] = 0;
}

int main( int argc, char **argv )
{
    HRESULT hr;

    if (argc < 2)
    {
        fprintf( stderr, "usage: xboxbridge import | get URL [CONTRACT] | keys CONTENTID [MARKET] | owns PRODUCTID | importclient ID | hasclient ID\n" );
        return 2;
    }
    _setmode( _fileno( stdout ), _O_BINARY );
    auth = LoadLibraryA( "winexboxauth.dll" );

    if (!strcmp( argv[1], "import" ))
    {
        static char refresh[16384], user[256];
        import_fn import = (import_fn)need( "XboxAuthImportRefreshToken" );
        if (!fgets( refresh, sizeof(refresh), stdin )) return 2;
        if (!fgets( user, sizeof(user), stdin )) user[0] = 0;
        chomp( refresh );
        chomp( user );
        hr = import( refresh, user );
        SecureZeroMemory( refresh, sizeof(refresh) );
        if (FAILED(hr)) fprintf( stderr, "xboxbridge: import failed, %#lx\n", hr );
        return FAILED(hr) ? 1 : 0;
    }
    if (!strcmp( argv[1], "get" ) && argc >= 3)
    {
        get_fn get = (get_fn)need( "XboxAuthHttpGet" );
        unsigned char *body = NULL;
        UINT32 len = 0;
        DWORD status = 0;

        CoInitializeEx( NULL, COINIT_MULTITHREADED );
        hr = get( argv[2], "Accept: application/json", argc >= 4 ? argv[3] : "3", &body, &len, &status );
        if (FAILED(hr))
        {
            fprintf( stderr, "xboxbridge: request failed, %#lx\n", hr );
            return 1;
        }
        printf( "STATUS %lu\n", status );
        if (body)
        {
            fwrite( body, 1, len, stdout );
            CoTaskMemFree( body );
        }
        return 0;
    }
    if (!strcmp( argv[1], "keys" ) && argc >= 3)
    {
        static char keys[65536];
        keys_fn fn = (keys_fn)need( "XboxAuthContentKeys" );

        CoInitializeEx( NULL, COINIT_MULTITHREADED );
        hr = fn( argv[2], argc >= 4 ? argv[3] : "US", keys, sizeof(keys) );
        if (FAILED(hr))
        {
            fprintf( stderr, "xboxbridge: no content keys, %#lx\n", hr );
            return 1;
        }
        fputs( keys, stdout );
        fflush( stdout );
        SecureZeroMemory( keys, sizeof(keys) );
        return 0;
    }
    if (!strcmp( argv[1], "owns" ) && argc >= 3)
    {
        owns_fn fn = (owns_fn)need( "XboxAuthOwnsProduct" );
        CoInitializeEx( NULL, COINIT_MULTITHREADED );
        hr = fn( argv[2] );
        if (FAILED(hr))
        {
            fprintf( stderr, "xboxbridge: ownership check failed, %#lx\n", hr );
            return 1;
        }
        puts( hr == S_OK ? "owned" : "not owned" );
        return 0;
    }
    if (!strcmp( argv[1], "importclient" ) && argc >= 3)
    {
        static char refresh[16384];
        import_client_fn fn = (import_client_fn)need( "XboxAuthImportClientRefreshToken" );
        if (!fgets( refresh, sizeof(refresh), stdin )) return 2;
        chomp( refresh );
        hr = fn( argv[2], refresh );
        SecureZeroMemory( refresh, sizeof(refresh) );
        if (FAILED(hr)) fprintf( stderr, "xboxbridge: client import failed, %#lx\n", hr );
        return FAILED(hr) ? 1 : 0;
    }
    if (!strcmp( argv[1], "hasclient" ) && argc >= 3)
    {
        has_client_fn fn = (has_client_fn)need( "XboxAuthHasClient" );
        hr = fn( argv[2] );
        if (FAILED(hr))
        {
            fprintf( stderr, "xboxbridge: client check failed, %#lx\n", hr );
            return 1;
        }
        puts( hr == S_OK ? "yes" : "no" );
        return 0;
    }
    fprintf( stderr, "xboxbridge: unknown command %s\n", argv[1] );
    return 2;
}
