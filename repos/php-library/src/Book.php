<?php

namespace App;

class Book
{
    public int $id;
    public string $title;
    public string $author;
    public int $totalCopies;
    public int $availableCopies;

    public function __construct(int $id, string $title, string $author, int $totalCopies)
    {
        $this->id = $id;
        $this->title = $title;
        $this->author = $author;
        $this->totalCopies = $totalCopies;
        $this->availableCopies = $totalCopies;
    }
}
