#!/usr/bin/perl
# ATLAS installer window server (macOS). Tiny, local-only HTTP server: serves ui.html, hands out state.json,
# and writes the user's answers to answer.json. Perl ships with macOS, so this works before Python is installed.
#   ui-server.pl <dir> <token>        (prints the port number to <dir>/port, runs until <dir>/stop exists or its parent dies)
use strict; use warnings; use IO::Socket::INET;
my ($dir, $token) = @ARGV;
die "usage: ui-server.pl DIR TOKEN\n" unless defined $dir && defined $token && length $token >= 16;
my $parent = getppid();
my $srv = IO::Socket::INET->new(Listen => 8, LocalAddr => '127.0.0.1', LocalPort => 0, ReuseAddr => 1, Proto => 'tcp') or die "listen: $!";
my $port = $srv->sockport();
open(my $pf, '>', "$dir/port.tmp") or die; print $pf $port; close $pf; rename "$dir/port.tmp", "$dir/port";
sub slurp { my ($f) = @_; open(my $h, '<:raw', $f) or return undef; local $/; my $d = <$h>; close $h; return $d; }
sub reply { my ($c, $code, $type, $body) = @_;
  $body = '' unless defined $body; my $len = length $body;
  print $c "HTTP/1.1 $code\r\nContent-Type: $type\r\nContent-Length: $len\r\nCache-Control: no-store\r\nConnection: close\r\nX-Content-Type-Options: nosniff\r\n\r\n$body"; }
$srv->timeout(1);
while (1) {
  last if -e "$dir/stop";
  last unless kill(0, $parent);
  my $c = $srv->accept() or next;
  $c->timeout(5);
  my $req = <$c>; unless (defined $req) { close $c; next; }
  my ($method, $target) = $req =~ m{^(\w+)\s+(\S+)\s+HTTP};
  my ($len, $host) = (0, '');
  while (my $l = <$c>) { last if $l =~ /^\r?\n$/; $len = $1 if $l =~ /^content-length:\s*(\d+)/i; $host = $1 if $l =~ /^host:\s*(\S+)/i; }
  my $body = ''; if ($len > 0 && $len < 200000) { read($c, $body, $len); }
  my ($path, $q) = split /\?/, ($target // ''), 2; $q //= '';
  my ($t) = $q =~ /(?:^|&)t=([A-Za-z0-9]+)/;
  if (!$method || $host !~ /^(127\.0\.0\.1|localhost):$port$/ || !defined $t || $t ne $token) { reply($c, '403 Forbidden', 'text/plain', 'forbidden'); close $c; next; }
  if ($method eq 'GET' && $path eq '/') { reply($c, '200 OK', 'text/html; charset=utf-8', slurp("$dir/ui.html") // 'missing ui'); }
  elsif ($method eq 'GET' && $path eq '/state') { reply($c, '200 OK', 'application/json', slurp("$dir/state.json") // '{"phase":"starting"}'); }
  elsif ($method eq 'POST' && $path eq '/answer') {
    if (open(my $a, '>:raw', "$dir/answer.tmp")) { print $a $body; close $a; rename "$dir/answer.tmp", "$dir/answer.json"; }
    reply($c, '200 OK', 'application/json', '{"ok":true}'); }
  else { reply($c, '404 Not Found', 'text/plain', 'no'); }
  # every request counts as "the window is still open"
  if (open(my $s, '>', "$dir/seen")) { print $s time(); close $s; }
  close $c;
}
