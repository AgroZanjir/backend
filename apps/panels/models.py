"""The panels app owns no tables.

A panel is one audience's way through several clusters - the bank panel reads
lots, storage and finance; the lot passport shows quality, storage and finance
on one page. Composition of that kind has to live somewhere, and the choice is
between letting clusters import each other (which ends the independence rule
in a fortnight) or giving the composition its own module.

This is that module: serializers and read views that span clusters, and
nothing else. It is the only place in the backend where an import crosses from
one outer cluster to another, and it may never be imported *by* a cluster.
"""
